package com.chlansgus.ktx.core

import java.math.BigInteger
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * korail2 포크(dhfhfk/korail2@4b13426)의 DynaPathMasterEngine을 그대로 옮긴 것입니다.
 * 코레일 앱과 같은 x-dynapath-m-token 헤더를 만듭니다. 결과는 Python 구현과 테스트로 대조합니다.
 */
class DynaPathEngine(val appStartTs: String = System.currentTimeMillis().toString()) {

    fun generateToken(deviceId: String, ts: Long, rand: String): String {
        val plaintext = "ai=$APP_ID&di=$deviceId&as=$AS_VALUE&" +
            "su=false&dbg=false&emu=false&hk=false&it=$appStartTs&" +
            "ts=$ts&rt=0&os=13&dm=$DEVICE_MODEL&st=$OS_TYPE&sv=$SDK_VERSION"
        val dynKey = "v1+$rand+$ts"
        val keyEnc = encodeNormalBe(dynKey, TABLE)
        val customTable = makeEncodeTable(makeKey(dynKey), I9, TABLE)
        val bodyEnc = encodeNormalBe(plaintext, customTable)
        return "bEeEP${TABLE[keyEnc.length]}$keyEnc$bodyEnc"
    }

    companion object {
        const val APP_ID = "com.korail.talk"
        const val AS_VALUE = "%5B38ff229cb34c7dda8e28220a2d750cce%5D"
        const val DEVICE_MODEL = "SM-S928N"
        const val OS_TYPE = "Android"
        const val SDK_VERSION = "v1"
        const val TABLE = "3FE9jgRD4KdCyuawklqGJYmvfMn15P7US8XbxeLQtWT6OicBAopINs2Vh0HZrz"
        private const val I8 = 161
        private const val I9 = 30
        private const val I10 = 2

        internal fun string2xA1s(data: String): List<Int> {
            val result = ArrayList<Int>()
            data.codePoints().forEach { cp ->
                when {
                    cp < 128 -> result.add(cp)
                    cp < 2048 -> {
                        result.add(128 or ((cp shr 7) and 15))
                        result.add(cp and 127)
                    }
                    cp >= 262144 -> {
                        result.add(160)
                        result.add((cp shr 14) and 127)
                        result.add((cp shr 7) and 127)
                        result.add(cp and 127)
                    }
                    (63488 and cp) != 55296 -> {
                        result.add(((cp shr 14) and 15) or 144)
                        result.add((cp shr 7) and 127)
                        result.add(cp and 127)
                    }
                }
            }
            return result
        }

        internal fun makeKey(key: String): BigInteger {
            var total = BigInteger.ZERO
            key.codePoints().forEach { cp ->
                var bit = 32768
                for (i in 0 until 16) {
                    if (bit and cp != 0) break
                    bit = bit shr 1
                }
                total = total.multiply(BigInteger.valueOf((bit shl 1).toLong())).add(BigInteger.valueOf(cp.toLong()))
            }
            return total
        }

        internal fun makeEncodeTable(num: BigInteger, size: Int, baseTable: String): String {
            val sb = StringBuilder()
            var rest = num
            for (i in 0 until size) {
                val divisor = BigInteger.valueOf((size - i).toLong())
                val remainder = rest.mod(divisor).toInt()
                var count = 0
                var picked = ' '
                for (c in baseTable) {
                    if (sb.indexOf(c) >= 0) continue
                    if (count == remainder) { picked = c; break }
                    count++
                }
                sb.append(picked)
                rest = rest.divide(divisor)
            }
            return sb.toString()
        }

        internal fun encodeNormalBe(data: String, table: String): String {
            val list = string2xA1s(data)
            val sb = StringBuilder()
            val digits = IntArray(I10 + 1)
            var idx = 0
            var size = list.size % I10
            val size2 = list.size - size
            while (idx < size2) {
                var v = 0
                repeat(I10) { v = v * I8 + list[idx++] }
                for (i in 0..I10) { digits[i] = v % I9; v /= I9 }
                for (i in I10 downTo 0) sb.append(table[digits[i]])
            }
            if (size > 0) {
                var v = 0
                repeat(size) { v = v * I8 + list[idx++] }
                for (i in 0..size) { digits[i] = v % I9; v /= I9 }
                while (size >= 0) { sb.append(table[digits[size]]); size-- }
            }
            return sb.toString()
        }
    }
}

internal object KorailCrypto {
    private val SID_KEY = "2485dd54d9deaa36".toByteArray()

    fun sid(device: String, ts: Long): String {
        val cipher = Cipher.getInstance("AES/CBC/PKCS5Padding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(SID_KEY, "AES"), IvParameterSpec(SID_KEY))
        return Base64.getEncoder().encodeToString(cipher.doFinal("$device$ts".toByteArray())) + "\n"
    }

    /** 서버가 내려준 key로 AES-CBC 암호화 후 base64를 두 번 적용합니다(korail2와 동일). */
    fun encryptPassword(password: String, key: String): String {
        val keyBytes = key.toByteArray()
        val cipher = Cipher.getInstance("AES/CBC/PKCS5Padding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(keyBytes, "AES"), IvParameterSpec(key.substring(0, 16).toByteArray()))
        val once = Base64.getEncoder().encode(cipher.doFinal(password.toByteArray()))
        return Base64.getEncoder().encodeToString(once)
    }
}
