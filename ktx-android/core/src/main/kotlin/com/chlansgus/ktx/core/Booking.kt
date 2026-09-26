package com.chlansgus.ktx.core

import java.io.IOException
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.random.Random

val KST: ZoneId = ZoneId.of("Asia/Seoul")

enum class SeatGrade(val label: String) { GENERAL("일반실"), SPECIAL("특실") }

/** 검색조건. 날짜 yyyyMMdd, 시간 HHmmss (종료시간은 해당 분의 59초까지 포함). */
data class Conditions(
    val departure: String,
    val arrival: String,
    val date: String,
    val start: String,
    val end: String,
    val count: Int,
    val grade: SeatGrade,
) {
    companion object {
        private val DATE = DateTimeFormatter.ofPattern("uuuuMMdd").withResolverStyle(ResolverStyle.STRICT)
        private val TIME = DateTimeFormatter.ofPattern("HHmm").withResolverStyle(ResolverStyle.STRICT)

        fun parse(
            departure: String, arrival: String, date: String, start: String, end: String,
            count: String, grade: SeatGrade, today: LocalDate = LocalDate.now(KST),
        ): Conditions {
            val dep = departure.trim()
            val arr = arrival.trim()
            if (dep.isEmpty() || arr.isEmpty() || dep == arr) {
                throw IllegalArgumentException("서로 다른 출발역과 도착역을 입력하세요. (예: 서울, 부산)")
            }
            val d = date.trim().replace("-", "")
            val s = start.trim().replace(":", "")
            val e = end.trim().replace(":", "")
            if (!Regex("\\d{8}").matches(d)) throw IllegalArgumentException("날짜는 YYYY-MM-DD 또는 YYYYMMDD 형식으로 입력하세요.")
            val day = try {
                listOf(s, e).forEach {
                    if (!Regex("\\d{4}").matches(it)) throw IllegalArgumentException()
                    LocalTime.parse(it, TIME)
                }
                LocalDate.parse(d, DATE)
            } catch (ex: Exception) {
                throw IllegalArgumentException("실제 날짜와 00:00~23:59 범위의 시간을 입력하세요.")
            }
            if (day < today || s > e) throw IllegalArgumentException("과거 날짜는 사용할 수 없으며 시작시간은 종료시간 이하여야 합니다.")
            val n = count.trim()
            if (!Regex("\\d{1,2}").matches(n) || n.toInt() !in 1..9) throw IllegalArgumentException("인원은 성인 1~9명으로 입력하세요.")
            return Conditions(dep, arr, d, s + "00", e + "59", n.toInt(), grade)
        }
    }
}

/** 중지 신호. 대기는 즉시 깨우지만, 이미 보낸 HTTP 요청은 끝까지 기다립니다. */
class StopSignal {
    private val latch = CountDownLatch(1)
    fun set() = latch.countDown()
    val isSet get() = latch.count == 0L
    /** @return 대기 중 중지되었으면 true */
    fun await(ms: Long): Boolean = latch.await(ms, TimeUnit.MILLISECONDS)
}

sealed interface BookingEvent {
    data class Trains(val conditions: Conditions, val trains: List<Train>) : BookingEvent
    data class Status(val text: String) : BookingEvent
    data class Reserved(val reservation: Reservation) : BookingEvent
}

fun errorText(e: Throwable): String = when (e) {
    is IOException -> "통신 오류 또는 응답 시간 초과입니다. 인터넷 연결과 코레일 서비스 상태를 확인하세요."
    else -> e.message?.takeIf { it.isNotBlank() } ?: e.javaClass.simpleName
}

/** API가 한 번에 일부만 주므로 마지막 열차 출발시간 이후로 이어서 종료시간까지 조회합니다. */
fun searchTrains(client: KorailClient, c: Conditions, stop: StopSignal, pageDelayMs: Long = 1000): List<Train> {
    val found = LinkedHashMap<Triple<String, String, String>, Train>()
    var cursor = c.start
    while (cursor <= c.end && !stop.isSet) {
        val page = try {
            client.searchTrain(c.departure, c.arrival, c.date, cursor, c.count)
        } catch (e: NoResultsException) {
            break
        }
        if (stop.isSet) break
        val sameDay = page.filter { it.depDate == c.date }
        if (sameDay.isEmpty()) break
        sameDay.filter { it.depTime in c.start..c.end }.forEach { found[Triple(it.trainNo, it.depDate, it.depTime)] = it }
        val last = sameDay.maxOf { it.depTime }
        if (last >= c.end || last < cursor) break
        val next = LocalTime.parse(last, DateTimeFormatter.ofPattern("HHmmss")).plusSeconds(1)
        if (next == LocalTime.MIDNIGHT) break
        cursor = next.format(DateTimeFormatter.ofPattern("HHmmss"))
        if (stop.await(pageDelayMs)) break
    }
    return found.values.sortedWith(compareBy({ it.depTime }, { it.trainNo }))
}

fun reserveTrain(client: KorailClient, train: Train, c: Conditions): Reservation =
    client.reserve(train, c.count, c.grade == SeatGrade.SPECIAL)

/**
 * 조건에 맞는 열차를 출발시간순으로 확인해 좌석이 있는 첫 열차를 예약합니다.
 * 매진이면 1~3초 뒤 재조회하고, 예약 성공 즉시 끝납니다. 결과가 불확실한 오류는 재시도하지 않습니다.
 */
fun autoReserve(
    client: KorailClient,
    c: Conditions,
    stop: StopSignal,
    emit: (BookingEvent) -> Unit,
    now: () -> LocalDateTime = { LocalDateTime.now(KST) },
    retryDelayMs: () -> Long = { Random.nextLong(1000, 3001) },
    pageDelayMs: Long = 1000,
) {
    var attempt = 0
    val stamp = DateTimeFormatter.ofPattern("yyyyMMddHHmmss")
    while (!stop.isSet) {
        if (c.date + c.end < now().format(stamp)) throw IllegalStateException("설정한 출발시간 범위가 지났습니다.")
        attempt++
        val trains = searchTrains(client, c, stop, pageDelayMs)
        emit(BookingEvent.Trains(c, trains))
        for (train in trains) {
            if (stop.isSet) return
            val available = if (c.grade == SeatGrade.GENERAL) train.hasGeneralSeat() else train.hasSpecialSeat()
            if (!available) continue
            val reservation = try {
                reserveTrain(client, train, c)
            } catch (e: SoldOutException) {
                emit(BookingEvent.Status("예약 시도 중 매진되었습니다. 다시 조회합니다."))
                continue
            } catch (e: Exception) {
                throw IllegalStateException(
                    "예약 실패: ${errorText(e)}\n결과가 불확실하므로 코레일 예약내역을 먼저 확인하세요. 자동 재시도하지 않습니다.",
                )
            }
            stop.set()
            emit(BookingEvent.Reserved(reservation))
            return
        }
        val delay = retryDelayMs()
        emit(BookingEvent.Status("${attempt}회 조회 · 예약 가능한 좌석 없음 · ${"%.1f".format(delay / 1000.0)}초 후 재조회"))
        if (stop.await(delay)) return
    }
}
