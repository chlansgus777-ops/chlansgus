package com.chlansgus.ktx.core

import org.junit.Test
import kotlin.test.assertEquals

/** 기대값은 원본 Python korail2(dhfhfk@4b13426)로 생성했습니다. */
class CryptoTest {
    private val engine = DynaPathEngine("1758844800000")

    @Test
    fun tokenMatchesPython() {
        assertEquals(
            "bEeEPSYj1Dm5CMM4Pv4ffKkKdE4Kd54GDK3FFmvk5gqEPGl3lCPGmvk5Pg9EwuPCaM13ywnjnMlYujDk3P33Pl3Jujn3ya3M1aEPMMElgw1lYdyj5jqRvJvjaCvu9yqqvDCyYfl5M3GdvDRvdmjnFvJyyY3jnfM1uEGdylPPCCyYjvGdykJylPPCCyYjyaEEGdylPPCCyYjyffgquvkaEEnM1CEJv3JJjnjl53jld3PM3PM3yKEKglGqjKdl5DjlJl593JPjaCM14EJv3yKPGnlGqj9Gyqjgq9CJ4k1ljqMCGKEfFlGnPuEEmmyKwM1ufajf91",
            engine.generateToken("558a4f02041657ea", 1758844801234, "AB12"),
        )
        assertEquals(
            "bEeEPSYj1Dvvd41dDd4ffdDw4GR4GR4GR4GRFqwkJgy3jC9K9GjCuwkJjgv3m1jG4EdKYmMDME951DPkKjKKj9KF1DMKY4KEd43jEE39gmd95RYDJDynwFwD4Gw1vYyywPGY5q9JEKCRwPnwRuDMfwFYY5KDMqEd13CRY9jjGGY5DwCRYkFY9jjGGY5DY433CRY9jjGGY5DYqqgy1wk433MEdG3FwKFFDMD9JKD9RKjEKjEKYl3lg9CyDdJKjEKjEKjEKjEKjEEda3FwKYljCM9CyDvCYyDgyvGFakd9DyEGCl3qf9CMj133uuYlmEd1q4Dqvd",
            engine.generateToken("558a4f02041657ea", 1790000000000, "Z9Z9"),
        )
    }

    @Test
    fun helpersMatchPython() {
        val key = DynaPathEngine.makeKey("v1+AB12+1758844801234")
        assertEquals("631522856407811124938713795763873721588", key.toString())
        assertEquals("qF5Y9aMR3jlgDCkmnvyPEf4uJwK1dG", DynaPathEngine.makeEncodeTable(key, 30, DynaPathEngine.TABLE))
        assertEquals("nRa3jKq5uylPluM9K", DynaPathEngine.encodeNormalBe("가é𒍅abc", DynaPathEngine.TABLE))
    }

    @Test
    fun sidAndPasswordMatchPython() {
        assertEquals("B+8wHRYQ8RwU3tACA53DVw==\n", KorailCrypto.sid("AD", 1758844801234))
        assertEquals(
            "UldyeUVyTXIwYUVpMGxTZmpueE4xZz09",
            KorailCrypto.encryptPassword("p@ss한글1", "0123456789abcdef0123456789abcdef"),
        )
    }
}
