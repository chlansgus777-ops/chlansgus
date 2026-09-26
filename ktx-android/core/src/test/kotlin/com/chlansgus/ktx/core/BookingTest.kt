package com.chlansgus.ktx.core

import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs
import kotlin.test.assertTrue

class BookingTest {
    private val server = MockWebServer().apply { start() }
    private val client = KorailClient(server.url("").toString().trimEnd('/'), minIntervalMs = 0)
    private val today = LocalDate.of(2030, 1, 1)
    private val cond = Conditions.parse("서울", "부산", "2030-01-01", "06:00", "09:00", "1", SeatGrade.GENERAL, today)

    @After fun tearDown() = server.shutdown()

    @Test
    fun parseNormalizesAndValidates() {
        assertEquals(Conditions("서울", "부산", "20300101", "060000", "090059", 1, SeatGrade.GENERAL), cond)
        fun bad(vararg v: String) = assertFailsWith<IllegalArgumentException> {
            Conditions.parse(v[0], v[1], v[2], v[3], v[4], v[5], SeatGrade.GENERAL, today)
        }
        bad("서울", "서울", "20300101", "0600", "0900", "1")
        bad("서울", "부산", "2030-02-30", "0600", "0900", "1")
        bad("서울", "부산", "20291231", "0600", "0900", "1")
        bad("서울", "부산", "20300101", "2400", "2400", "1")
        bad("서울", "부산", "20300101", "1000", "0900", "1")
        bad("서울", "부산", "20300101", "0600", "0900", "10")
        bad("서울", "부산", "20300101", "0600", "0900", "0")
    }

    @Test
    fun searchPagesUntilEndAndFiltersRange() {
        server.enqueue(ok(searchJson(trainJson("1", "055000"), trainJson("2", "060000"), trainJson("3", "070000"))))
        server.enqueue(ok(searchJson(trainJson("3", "070000"), trainJson("4", "080000"), trainJson("5", "100000"))))
        val trains = searchTrains(client, cond, StopSignal(), pageDelayMs = 0)
        assertEquals(listOf("2", "3", "4"), trains.map { it.trainNo })
        server.takeRequest()
        assertEquals("070001", server.takeRequest().requestUrl!!.queryParameter("txtGoHour"))
        assertEquals(2, server.requestCount)
    }

    @Test
    fun autoReserveRetriesSoldOutThenStopsOnSuccess() {
        server.enqueue(ok(searchJson(trainJson("1", "060000", gen = "13"), trainJson("9", "235000"))))
        server.enqueue(ok(searchJson(trainJson("1", "060000", gen = "11"), trainJson("9", "235000"))))
        server.enqueue(ok(SOLD_OUT))
        server.enqueue(ok(searchJson(trainJson("1", "060000", gen = "11"), trainJson("9", "235000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        val events = mutableListOf<BookingEvent>()
        val stop = StopSignal()
        autoReserve(client, cond, stop, { events += it }, now = { LocalDateTime.of(2030, 1, 1, 0, 0) },
            retryDelayMs = { 0 }, pageDelayMs = 0)
        val reserved = events.last()
        assertIs<BookingEvent.Reserved>(reserved)
        assertEquals("PNR123", reserved.reservation.rsvId)
        assertTrue(stop.isSet)
        assertTrue(events.any { it is BookingEvent.Status && it.text.contains("매진") })
    }

    private fun run(stop: StopSignal = StopSignal(), relogin: (() -> Boolean)? = null, events: MutableList<BookingEvent> = mutableListOf()) =
        autoReserve(client, cond, stop, { events += it }, relogin = relogin, now = { LocalDateTime.of(2030, 1, 1, 0, 0) },
            retryDelayMs = { 0 }, pageDelayMs = 0, backoffMs = { 0 }, verifyDelayMs = 0)

    @Test
    fun uncertainReserveErrorIsVerifiedAgainstReservations() {
        server.enqueue(ok(searchJson(trainJson("101", "060000"), trainJson("9", "235000"))))
        server.enqueue(ok("{}").setResponseCode(500))
        server.enqueue(ok(RESERVATIONS))
        val events = mutableListOf<BookingEvent>()
        run(events = events)
        val r = events.last()
        assertIs<BookingEvent.Reserved>(r)
        assertEquals("PNR123", r.reservation.rsvId)
    }

    @Test
    fun uncertainReserveErrorContinuesWhenNotReserved() {
        server.enqueue(ok(searchJson(trainJson("101", "060000"), trainJson("9", "235000"))))
        server.enqueue(ok("{}").setResponseCode(500))
        server.enqueue(ok(NO_RESULTS)) // 예약내역 없음 = 예약 안 됨
        server.enqueue(ok(searchJson(trainJson("101", "060000"), trainJson("9", "235000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        val events = mutableListOf<BookingEvent>()
        run(events = events)
        assertIs<BookingEvent.Reserved>(events.last())
        assertEquals(6, server.requestCount)
    }

    @Test
    fun stopsWhenReservationCannotBeVerified() {
        server.enqueue(ok(searchJson(trainJson("101", "060000"), trainJson("9", "235000"))))
        server.enqueue(ok("{}").setResponseCode(500))
        server.enqueue(ok("{}").setResponseCode(500))
        val e = assertFailsWith<IllegalStateException> { run() }
        assertTrue(e.message!!.startsWith("예약 실패"))
        assertEquals(3, server.requestCount)
    }

    @Test
    fun expiredSessionReloginsAndContinues() {
        server.enqueue(ok("""{"strResult":"FAIL","h_msg_cd":"P058","h_msg_txt":"로그인 필요"}"""))
        server.enqueue(ok(searchJson(trainJson("101", "060000"), trainJson("9", "235000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        var relogins = 0
        val events = mutableListOf<BookingEvent>()
        run(relogin = { relogins++; true }, events = events)
        assertEquals(1, relogins)
        assertIs<BookingEvent.Reserved>(events.last())
    }

    @Test
    fun expiredSessionWithoutAutoLoginStops() {
        server.enqueue(ok("""{"strResult":"FAIL","h_msg_cd":"P058","h_msg_txt":"로그인 필요"}"""))
        val e = assertFailsWith<IllegalStateException> { run() }
        assertTrue(e.message!!.contains("로그인이 만료"))
    }

    @Test
    fun transientSearchErrorsBackOffThenGiveUp() {
        server.enqueue(ok("{}").setResponseCode(503))
        server.enqueue(ok("<html>점검</html>"))
        server.enqueue(ok(searchJson(trainJson("101", "060000"), trainJson("9", "235000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        val events = mutableListOf<BookingEvent>()
        run(events = events)
        assertIs<BookingEvent.Reserved>(events.last())
        assertEquals(2, events.count { it is BookingEvent.Status && it.text.startsWith("일시적인 오류") })

        repeat(7) { server.enqueue(ok("{}").setResponseCode(503)) }
        val e = assertFailsWith<IllegalStateException> { run() }
        assertTrue(e.message!!.startsWith("통신 오류가 계속"))
    }

    @Test
    fun appUpdateRejectionIsNotRetried() {
        server.enqueue(ok("""{"strResult":"FAIL","h_msg_cd":"WRR800029","h_msg_txt":"앱을 최신 버전으로 업데이트"}"""))
        assertFailsWith<KorailException> { run() }
        assertEquals(1, server.requestCount)
    }

    @Test
    fun autoReserveRefusesPastRange() {
        assertFailsWith<IllegalStateException> {
            autoReserve(client, cond, StopSignal(), {}, now = { LocalDateTime.of(2030, 1, 1, 9, 1) })
        }
    }

    @Test
    fun autoReserveBooksFromFirstPageWithoutWaitingForTheRest() {
        server.enqueue(ok(searchJson(trainJson("1", "060000", gen = "13"), trainJson("2", "061000"), trainJson("3", "070000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        val events = mutableListOf<BookingEvent>()
        autoReserve(client, cond, StopSignal(), { events += it }, now = { LocalDateTime.of(2030, 1, 1, 0, 0) }, retryDelayMs = { 0 }, pageDelayMs = 0)
        assertIs<BookingEvent.Reserved>(events.last())
        server.takeRequest()
        assertEquals("2", server.takeRequest().requestUrl!!.queryParameter("txtTrnNo1"))
        assertEquals(3, server.requestCount)
    }

    @Test
    fun autoReserveTargetsOnlySelectedTrainsAndNarrowsSearch() {
        // 2번은 좌석이 있어도 대상이 아니므로 건너뛰고, 조회는 대상 열차 시각(07:00)부터 시작합니다.
        server.enqueue(ok(searchJson(trainJson("3", "070000"), trainJson("4", "080000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        autoReserve(client, cond, StopSignal(), {}, targets = setOf("3/20300101/070000"),
            now = { LocalDateTime.of(2030, 1, 1, 0, 0) }, retryDelayMs = { 0 }, pageDelayMs = 0)
        assertEquals("070000", server.takeRequest().requestUrl!!.queryParameter("txtGoHour"))
        assertEquals("3", server.takeRequest().requestUrl!!.queryParameter("txtTrnNo1"))
    }

    @Test
    fun anyGradeFallsBackToSpecial() {
        val any = cond.copy(grade = SeatGrade.ANY)
        server.enqueue(ok(searchJson(trainJson("1", "060000", gen = "13", spe = "11"), trainJson("9", "235000"))))
        server.enqueue(ok(RESERVE_OK))
        server.enqueue(ok(RESERVATIONS))
        autoReserve(client, any, StopSignal(), {}, now = { LocalDateTime.of(2030, 1, 1, 0, 0) }, retryDelayMs = { 0 }, pageDelayMs = 0)
        server.takeRequest()
        assertEquals("2", server.takeRequest().requestUrl!!.queryParameter("txtPsrmClCd1"))
    }

    @Test
    fun stopInterruptsWaitImmediately() {
        server.enqueue(ok(NO_RESULTS))
        val stop = StopSignal()
        val t = Thread { autoReserve(client, cond, stop, {}, now = { LocalDateTime.of(2030, 1, 1, 0, 0) }, retryDelayMs = { 60_000 }) }
        t.start()
        Thread.sleep(500)
        val t0 = System.nanoTime()
        stop.set()
        t.join(5000)
        assertTrue(!t.isAlive && (System.nanoTime() - t0) < 2_000_000_000)
    }
}
