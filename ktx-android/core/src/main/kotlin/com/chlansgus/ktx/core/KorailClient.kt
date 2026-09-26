package com.chlansgus.ktx.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.FormBody
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * 코레일 모바일 API 클라이언트 (korail2 포크의 login/search_train/reserve/reservations 이식).
 * - 모든 HTTP 요청 시작 간격을 [minIntervalMs] 이상으로 유지합니다.
 * - 쿠키/토큰은 메모리에만 있고, 비밀번호는 로그인 요청에만 쓰고 보관하지 않습니다.
 */
class KorailClient(
    private val baseUrl: String = "https://smart.letskorail.com:443",
    private val minIntervalMs: Long = 1000,
    private val clock: () -> Long = System::currentTimeMillis,
    private val random: () -> String = { (1..4).map { RAND_CHARS.random() }.joinToString("") },
) {
    private val cookies = mutableMapOf<String, Cookie>()
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .cookieJar(object : CookieJar {
            override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) = synchronized(this@KorailClient.cookies) {
                cookies.forEach { this@KorailClient.cookies[it.name] = it }
            }

            override fun loadForRequest(url: HttpUrl) = synchronized(this@KorailClient.cookies) {
                this@KorailClient.cookies.values.filter { it.matches(url) }
            }
        })
        .build()
    private val engine = DynaPathEngine(clock().toString())
    private val json = Json { ignoreUnknownKeys = true }
    private var lastRequest = 0L
    private var key = "korail1234567890"
    private var idx: String? = null

    /** 마지막으로 서버가 FAIL로 응답했을 때의 메시지 (로그인 거절 사유 표시용). */
    @Volatile var serverError: String = ""
        private set
    @Volatile var loggedIn = false
        private set
    var memberName: String? = null
        private set

    /** @param userId 회원번호, 휴대폰번호(010-1234-5678) 또는 이메일 */
    fun login(userId: String, password: String): Boolean {
        val inputFlag = when {
            EMAIL_REGEX.containsMatchIn(userId) -> "5"
            PHONE_REGEX.containsMatchIn(userId) -> "4"
            else -> "2"
        }
        val encrypted = encryptPassword(password)
            ?: throw ServerException("로그인용 암호화 키를 받지 못했습니다. 잠시 후 다시 시도하세요.")
        val (headers, sid) = authHeaders(LOGIN)
        val form = linkedMapOf(
            "Device" to DEVICE,
            "Version" to VERSION,
            "txtInputFlg" to inputFlag,
            "txtMemberNo" to userId,
            "txtPwd" to encrypted,
        )
        idx?.let { form["idx"] = it }
        if (sid != null) form["Sid"] = sid
        val j = post(LOGIN, form, headers)
        loggedIn = j.str("strResult") == "SUCC" && j.str("strMbCrdNo") != null
        if (loggedIn) {
            key = j.str("Key") ?: key
            memberName = j.str("strCustNm")
        }
        return loggedIn
    }

    private fun encryptPassword(password: String): String? {
        val j = post(CODE, mapOf("code" to "app.login.cphd"))
        val info = j["app.login.cphd"] as? JsonObject
        if (j.str("strResult") != "SUCC" || info == null) return null
        idx = info.str("idx")
        val serverKey = info.str("key") ?: return null
        return KorailCrypto.encryptPassword(password, serverKey)
    }

    /** KTX 열차를 [time](HHmmss) 이후로 한 페이지 조회합니다. 매진 열차도 포함합니다. */
    fun searchTrain(dep: String, arr: String, date: String, time: String, adults: Int): List<Train> {
        val (headers, _) = authHeaders(SEARCH)
        val params = linkedMapOf(
            "Device" to DEVICE,
            "radJobId" to "1",
            "selGoTrain" to TRAIN_TYPE_KTX,
            "txtCardPsgCnt" to "0",
            "txtGdNo" to "",
            "txtGoAbrdDt" to date,
            "txtGoEnd" to arr,
            "txtGoHour" to time,
            "txtGoStart" to dep,
            "txtJobDv" to "",
            "txtMenuId" to "11",
            "txtPsgFlg_1" to adults.toString(),
            "txtPsgFlg_2" to "0",
            "txtPsgFlg_8" to "0",
            "txtPsgFlg_3" to "0",
            "txtPsgFlg_4" to "0",
            "txtPsgFlg_5" to "0",
            "txtSeatAttCd_2" to "000",
            "txtSeatAttCd_3" to "000",
            "txtSeatAttCd_4" to "015",
            "txtTrnGpCd" to TRAIN_TYPE_KTX,
            "Version" to VERSION,
        )
        val request = Request.Builder().url(url(SEARCH, params)).post(ByteArray(0).toRequestBody(null))
        headers.forEach { (k, v) -> request.header(k, v) }
        val j = execute(request)
        check(j)
        val trains = list(obj(j["trn_infos"])?.get("trn_info")).map(Train::parse)
        if (trains.isEmpty()) throw NoResultsException()
        return trains
    }

    /** 좌석이 없으면 [SoldOutException]. 성공하면 예약번호를 담은 [Reservation]을 돌려줍니다. */
    fun reserve(train: Train, adults: Int, special: Boolean): Reservation {
        if (if (special) !train.hasSpecialSeat() else !train.hasGeneralSeat()) throw SoldOutException()
        val (headers, _) = authHeaders(RESERVE)
        val params = linkedMapOf(
            "Device" to DEVICE, "Version" to VERSION, "Key" to key,
            "txtGdNo" to "", "txtJobId" to "1101", "txtTotPsgCnt" to adults.toString(),
            "txtSeatAttCd1" to "000", "txtSeatAttCd2" to "000", "txtSeatAttCd3" to "000",
            "txtSeatAttCd4" to "015", "txtSeatAttCd5" to "000",
            "hidFreeFlg" to "N", "txtStndFlg" to "N", "txtMenuId" to "11", "txtSrcarCnt" to "0", "txtJrnyCnt" to "1",
            "txtJrnySqno1" to "001", "txtJrnyTpCd1" to "11",
            "txtDptDt1" to train.depDate, "txtDptRsStnCd1" to train.depCode, "txtDptTm1" to train.depTime,
            "txtArvRsStnCd1" to train.arrCode, "txtTrnNo1" to train.trainNo, "txtRunDt1" to train.runDate,
            "txtTrnClsfCd1" to train.trainType, "txtPsrmClCd1" to if (special) "2" else "1",
            "txtTrnGpCd1" to train.trainGroup, "txtChgFlg1" to "",
            "txtJrnySqno2" to "", "txtJrnyTpCd2" to "", "txtDptDt2" to "", "txtDptRsStnCd2" to "",
            "txtDptTm2" to "", "txtArvRsStnCd2" to "", "txtTrnNo2" to "", "txtRunDt2" to "",
            "txtTrnClsfCd2" to "", "txtPsrmClCd2" to "", "txtChgFlg2" to "",
            "txtPsgTpCd1" to "1", "txtDiscKndCd1" to "000", "txtCompaCnt1" to adults.toString(),
            "txtCardCode_1" to "", "txtCardNo_1" to "", "txtCardPw_1" to "",
        )
        val request = Request.Builder().url(url(RESERVE, params)).get()
        headers.forEach { (k, v) -> request.header(k, v) }
        val j = execute(request)
        check(j)
        val rsvId = j.str("h_pnr_no") ?: throw ServerException("예약 응답에 예약번호가 없습니다. 코레일 예약내역을 확인하세요.")
        // 예약 자체는 성공했으므로 상세 조회가 실패해도 예약번호는 반드시 돌려줍니다.
        return runCatching { reservations().firstOrNull { it.rsvId == rsvId } }.getOrNull() ?: Reservation(rsvId)
    }

    fun reservations(): List<Reservation> {
        val params = mapOf("Device" to DEVICE, "Version" to VERSION, "Key" to key)
        val j = execute(Request.Builder().url(url(MY_RESERVATIONS, params)).get())
        try {
            check(j)
        } catch (e: NoResultsException) {
            return emptyList()
        }
        return list(obj(j["jrny_infos"])?.get("jrny_info")).flatMap { jrny ->
            list(obj(obj(jrny)?.get("train_infos"))?.get("train_info")).map(Reservation::parse)
        }
    }

    private fun check(j: JsonObject) {
        if (j.str("strResult") != "FAIL") return
        val code = j.str("h_msg_cd")
        when (code) {
            in NoResultsException.CODES -> throw NoResultsException(code)
            in NeedToLoginException.CODES -> throw NeedToLoginException(code)
            in SoldOutException.CODES -> throw SoldOutException(code)
            else -> throw KorailException(j.str("h_msg_txt") ?: "서버가 요청을 거절했습니다.", code)
        }
    }

    private fun authHeaders(path: String): Pair<Map<String, String>, String?> {
        if (path !in DYNAPATH_PATHS) return emptyMap<String, String>() to null
        val ts = clock()
        val token = engine.generateToken(DEVICE_ID, ts, random())
        return mapOf("x-dynapath-m-token" to token) to KorailCrypto.sid(DEVICE, ts)
    }

    private fun url(path: String, params: Map<String, String> = emptyMap()): HttpUrl {
        val builder = (baseUrl + path).toHttpUrl().newBuilder()
        params.forEach { (k, v) -> builder.addQueryParameter(k, v) }
        return builder.build()
    }

    private fun post(path: String, form: Map<String, String>, headers: Map<String, String> = emptyMap()): JsonObject {
        val body = FormBody.Builder().apply { form.forEach { (k, v) -> add(k, v) } }.build()
        val request = Request.Builder().url(url(path)).post(body)
        headers.forEach { (k, v) -> request.header(k, v) }
        return execute(request)
    }

    private fun execute(builder: Request.Builder): JsonObject {
        val request = builder.header("User-Agent", USER_AGENT).header("Accept", "*/*").build()
        synchronized(this) {
            val wait = minIntervalMs - (clock() - lastRequest)
            if (wait > 0) Thread.sleep(wait)
            lastRequest = clock()
        }
        http.newCall(request).execute().use { response ->
            if (response.code >= 400) {
                throw ServerException("서버 HTTP 오류 ${response.code}. 잠시 후 코레일에서 직접 확인하세요.")
            }
            val text = response.body?.string().orEmpty()
            val j = runCatching { json.parseToJsonElement(text) as JsonObject }.getOrNull()
                ?: throw ServerException("서버 응답을 해석할 수 없습니다. 보안 확인 또는 서비스 점검 여부를 코레일에서 확인하세요.")
            if (j.str("strResult") == "FAIL") {
                serverError = j.str("h_msg_txt") ?: j.str("strMsg") ?: "서버가 요청을 거절했습니다."
            }
            return j
        }
    }

    fun close() {
        synchronized(cookies) { cookies.clear() }
        loggedIn = false
        http.dispatcher.executorService.shutdown()
        http.connectionPool.evictAll()
    }

    private fun obj(e: JsonElement?) = e as? JsonObject
    private fun list(e: JsonElement?): List<JsonObject> = when (e) {
        is JsonArray -> e.mapNotNull { it as? JsonObject }
        is JsonObject -> listOf(e)
        else -> emptyList()
    }

    companion object {
        private const val MOBILE = "/classes/com.korail.mobile"
        const val LOGIN = "$MOBILE.login.Login"
        const val SEARCH = "$MOBILE.seatMovie.ScheduleView"
        const val RESERVE = "$MOBILE.certification.TicketReservation"
        const val MY_RESERVATIONS = "$MOBILE.reservation.ReservationView"
        const val CODE = "$MOBILE.common.code.do"
        private val DYNAPATH_PATHS = setOf(LOGIN, SEARCH, RESERVE)

        const val DEVICE = "AD"
        const val VERSION = "250601002"
        const val DEVICE_ID = "558a4f02041657ea"
        const val TRAIN_TYPE_KTX = "100"
        const val USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 13; SM-S928N Build/UP1A.231005.007)"
        private val RAND_CHARS = ('A'..'Z') + ('0'..'9')
        private val EMAIL_REGEX = Regex("^[^@]+@[^@]+\\.[^@]+")
        private val PHONE_REGEX = Regex("^(\\d{3})-(\\d{3,4})-(\\d{4})")
    }
}
