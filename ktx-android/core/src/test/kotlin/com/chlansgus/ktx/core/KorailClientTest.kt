package com.chlansgus.ktx.core

import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Test
import java.net.URLDecoder
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class KorailClientTest {
    private val server = MockWebServer().apply { start() }
    private val client = KorailClient(server.url("").toString().trimEnd('/'), minIntervalMs = 0, random = { "AB12" })

    @After fun tearDown() = server.shutdown()

    private fun form(body: String) = body.split("&").associate {
        val (k, v) = it.split("=", limit = 2)
        URLDecoder.decode(k, "UTF-8") to URLDecoder.decode(v, "UTF-8")
    }

    @Test
    fun loginSendsEncryptedPasswordTokenAndKeepsCookies() {
        server.enqueue(ok("""{"strResult":"SUCC","app.login.cphd":{"idx":"7","key":"0123456789abcdef0123456789abcdef"}}""")
            .addHeader("Set-Cookie", "JSESSIONID=abc; Path=/"))
        server.enqueue(ok("""{"strResult":"SUCC","strMbCrdNo":"1234567890","Key":"SESSIONKEY","strCustNm":"홍길동"}"""))
        server.enqueue(ok(searchJson(trainJson("101", "060000"))))

        assertTrue(client.login("010-1234-5678", "p@ss한글1"))

        val code = server.takeRequest()
        assertEquals(KorailClient.CODE, code.path)
        assertEquals("app.login.cphd", form(code.body.readUtf8())["code"])
        assertEquals(KorailClient.USER_AGENT, code.getHeader("User-Agent"))

        val login = server.takeRequest()
        assertEquals(KorailClient.LOGIN, login.path)
        assertTrue(login.getHeader("x-dynapath-m-token")!!.startsWith("bEeEP"))
        assertEquals("JSESSIONID=abc", login.getHeader("Cookie"))
        val f = form(login.body.readUtf8())
        assertEquals("4", f["txtInputFlg"])
        assertEquals("010-1234-5678", f["txtMemberNo"])
        assertEquals("UldyeUVyTXIwYUVpMGxTZmpueE4xZz09", f["txtPwd"])
        assertEquals("7", f["idx"])
        assertEquals("AD", f["Device"])
        assertTrue(f["Sid"]!!.endsWith("\n"))

        client.searchTrain("서울", "부산", "20300101", "060000", 2)
        val search = server.takeRequest()
        assertEquals("POST", search.method)
        val url = search.requestUrl!!
        assertEquals("서울", url.queryParameter("txtGoStart"))
        assertEquals("2", url.queryParameter("txtPsgFlg_1"))
        assertEquals("100", url.queryParameter("selGoTrain"))
        assertNotNull(search.getHeader("x-dynapath-m-token"))
    }

    @Test
    fun loginRejectedKeepsServerMessage() {
        server.enqueue(ok("""{"strResult":"SUCC","app.login.cphd":{"idx":"1","key":"0123456789abcdef0123456789abcdef"}}"""))
        server.enqueue(ok("""{"strResult":"FAIL","h_msg_txt":"비밀번호 오류"}"""))
        assertFalse(client.login("12345678", "x"))
        assertEquals("비밀번호 오류", client.serverError)
        server.takeRequest()
        assertEquals("2", form(server.takeRequest().body.readUtf8())["txtInputFlg"])
    }

    @Test
    fun reserveUsesSessionKeyAndReturnsDetails() {
        login()
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        val train = Train.parse(kotlinx.serialization.json.Json.parseToJsonElement(trainJson("101", "060000")) as kotlinx.serialization.json.JsonObject)
        val r = client.reserve(train, 1, special = false)
        assertEquals("PNR123", r.rsvId)
        assertEquals(59800, r.price)
        assertTrue(r.describe().contains("59,800원"))
        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("SESSIONKEY", req.requestUrl!!.queryParameter("Key"))
        assertEquals("1", req.requestUrl!!.queryParameter("txtPsrmClCd1"))
        assertEquals("101", req.requestUrl!!.queryParameter("txtTrnNo1"))
    }

    @Test
    fun reserveStillReturnsPnrWhenLookupFails() {
        login()
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok("not json"))
        val train = Train.parse(kotlinx.serialization.json.Json.parseToJsonElement(trainJson("101", "060000")) as kotlinx.serialization.json.JsonObject)
        val r = client.reserve(train, 1, special = false)
        assertEquals("PNR123", r.rsvId)
        assertFalse(r.confirmed)
    }

    @Test
    fun errorsAreMapped() {
        server.enqueue(ok(NO_RESULTS))
        assertFailsWith<NoResultsException> { client.searchTrain("서울", "부산", "20300101", "000000", 1) }
        server.enqueue(ok("<html>blocked</html>"))
        assertFailsWith<ServerException> { client.searchTrain("서울", "부산", "20300101", "000000", 1) }
        server.enqueue(ok("{}").setResponseCode(503))
        val e = assertFailsWith<ServerException> { client.searchTrain("서울", "부산", "20300101", "000000", 1) }
        assertTrue(e.message!!.contains("503"))
        server.enqueue(ok("""{"strResult":"FAIL","h_msg_cd":"WRR800029","h_msg_txt":"앱을 최신 버전으로 업데이트"}"""))
        val k = assertFailsWith<KorailException> { client.searchTrain("서울", "부산", "20300101", "000000", 1) }
        assertEquals("앱을 최신 버전으로 업데이트 (WRR800029)", k.message)
    }

    @Test
    fun soldOutTrainIsNotSent() {
        val train = Train.parse(kotlinx.serialization.json.Json.parseToJsonElement(trainJson("101", "060000", gen = "13")) as kotlinx.serialization.json.JsonObject)
        assertFailsWith<SoldOutException> { client.reserve(train, 1, special = false) }
        assertEquals(0, server.requestCount)
    }

    @Test
    fun requestsAreSpacedByMinimumInterval() {
        val slow = KorailClient(server.url("").toString().trimEnd('/'), minIntervalMs = 300)
        repeat(3) { server.enqueue(ok(NO_RESULTS)) }
        val t0 = System.nanoTime()
        repeat(3) { runCatching { slow.searchTrain("서울", "부산", "20300101", "000000", 1) } }
        assertTrue((System.nanoTime() - t0) / 1_000_000 >= 600)
    }

    private fun login() {
        server.enqueue(ok("""{"strResult":"SUCC","app.login.cphd":{"idx":"1","key":"0123456789abcdef0123456789abcdef"}}"""))
        server.enqueue(ok("""{"strResult":"SUCC","strMbCrdNo":"1","Key":"SESSIONKEY"}"""))
        client.login("12345678", "x")
        server.takeRequest(); server.takeRequest()
    }
}
