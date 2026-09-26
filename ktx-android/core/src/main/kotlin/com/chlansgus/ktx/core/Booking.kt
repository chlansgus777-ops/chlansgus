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

enum class SeatGrade(val label: String) {
    GENERAL("일반실"),
    SPECIAL("특실"),
    /** 일반실 우선, 없으면 특실 */
    ANY("일반실/특실");

    fun available(t: Train) = when (this) {
        GENERAL -> t.hasGeneralSeat()
        SPECIAL -> t.hasSpecialSeat()
        ANY -> t.hasSeat()
    }
}

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

/** 열차 식별 키 (열차번호 + 출발일 + 출발시각). */
val Train.key get() = "$trainNo/$depDate/$depTime"

/**
 * API가 한 번에 일부만 주므로 마지막 열차 출발시간 이후로 이어서 종료시간까지 조회합니다.
 * [onPage]는 페이지마다 새로 받은 범위 내 열차로 호출되며, true를 돌려주면 조회를 멈춥니다.
 */
fun searchTrains(
    client: KorailClient, c: Conditions, stop: StopSignal, pageDelayMs: Long = 1000,
    onPage: (List<Train>) -> Boolean = { false },
): List<Train> {
    val found = LinkedHashMap<String, Train>()
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
        val inRange = sameDay.filter { it.depTime in c.start..c.end }
        inRange.forEach { found[it.key] = it }
        if (onPage(inRange)) break
        val last = sameDay.maxOf { it.depTime }
        if (last >= c.end || last < cursor) break
        val next = LocalTime.parse(last, DateTimeFormatter.ofPattern("HHmmss")).plusSeconds(1)
        if (next == LocalTime.MIDNIGHT) break
        cursor = next.format(DateTimeFormatter.ofPattern("HHmmss"))
        if (stop.await(pageDelayMs)) break
    }
    return found.values.sortedWith(compareBy({ it.depTime }, { it.trainNo }))
}

/** ANY는 일반실이 있으면 일반실, 없으면 특실로 예약합니다. */
fun reserveTrain(client: KorailClient, train: Train, c: Conditions): Reservation {
    val special = when (c.grade) {
        SeatGrade.GENERAL -> false
        SeatGrade.SPECIAL -> true
        SeatGrade.ANY -> !train.hasGeneralSeat()
    }
    return client.reserve(train, c.count, special)
}

/** 부작용이 없는 조회 중 오류 중 잠시 뒤 다시 시도해도 되는 것 (통신 오류, 서버 일시 오류). */
private fun isTransient(e: Throwable) = e is IOException || e is ServerException

/** 예약 응답이 불확실할 때 예약내역에서 해당 열차 예약을 찾습니다. 조회 자체가 실패하면 예외를 던집니다. */
fun findReservation(client: KorailClient, train: Train): Reservation? =
    client.reservations().firstOrNull { r ->
        r.trainNo?.trimStart('0') == train.trainNo.trimStart('0') &&
            r.runDate == train.runDate && r.depTime == train.depTime
    }

/**
 * 조건에 맞는 열차를 출발시간순으로 확인해 좌석이 있는 첫 열차를 예약합니다.
 * - 페이지를 받는 즉시 좌석을 확인하고 바로 예약합니다(전체 조회를 기다리지 않음).
 * - [targets]가 있으면 그 열차만 노리고, 조회 범위도 그 열차들의 출발시각으로 좁혀 재확인 주기를 줄입니다.
 * - 로그인이 만료되면 [relogin]으로 다시 로그인해 이어갑니다(없으면 중지).
 * - 조회 중 통신 오류는 간격을 늘려 재시도하고, 연속 [maxFailures]회를 넘으면 중지합니다.
 * - 예약 응답이 불확실하면 예약내역을 조회해 실제 성공 여부를 확인합니다. 확인할 수 없을 때만 중지합니다.
 * 매진이면 1~3초 뒤 재조회하고, 예약 성공 즉시 끝납니다. 요청 간격 1초 제한은 [KorailClient]가 지킵니다.
 */
fun autoReserve(
    client: KorailClient,
    c: Conditions,
    stop: StopSignal,
    emit: (BookingEvent) -> Unit,
    targets: Set<String> = emptySet(),
    relogin: (() -> Boolean)? = null,
    now: () -> LocalDateTime = { LocalDateTime.now(KST) },
    retryDelayMs: () -> Long = { Random.nextLong(1000, 3001) },
    pageDelayMs: Long = 1000,
    backoffMs: (Int) -> Long = { n -> minOf(60_000L, 5_000L shl (n - 1)) },
    verifyDelayMs: Long = 3000,
    maxFailures: Int = 6,
) {
    val search = if (targets.isEmpty()) c else {
        val times = targets.map { it.substringAfterLast('/') }
        c.copy(start = maxOf(c.start, times.min()), end = minOf(c.end, times.max()))
    }
    var attempt = 0
    var failures = 0
    var relogins = 0
    val stamp = DateTimeFormatter.ofPattern("yyyyMMddHHmmss")
    while (!stop.isSet) {
        if (search.date + search.end < now().format(stamp)) throw IllegalStateException("설정한 출발시간 범위가 지났습니다.")
        attempt++
        var reservation: Reservation? = null
        val trains = try {
            searchTrains(client, search, stop, pageDelayMs) { page ->
                for (train in page) {
                    if (stop.isSet) return@searchTrains true
                    if (targets.isNotEmpty() && train.key !in targets) continue
                    if (!c.grade.available(train)) continue
                    try {
                        reservation = reserveTrain(client, train, c)
                        return@searchTrains true
                    } catch (e: SoldOutException) {
                        emit(BookingEvent.Status("${train.trainNo}열차 예약 시도 중 매진되었습니다. 계속 확인합니다."))
                    } catch (e: NeedToLoginException) {
                        throw e // 서버가 처리 전에 거절한 것이므로 재로그인 후 다시 시도해도 안전합니다.
                    } catch (e: Exception) {
                        emit(BookingEvent.Status("예약 응답 확인 중… 예약내역에서 실제 결과를 조회합니다."))
                        stop.await(verifyDelayMs)
                        val found = try {
                            findReservation(client, train)
                        } catch (v: Exception) {
                            throw IllegalStateException(
                                "예약 실패: ${errorText(e)}\n예약내역도 확인하지 못해 결과가 불확실합니다. 코레일 예약내역을 먼저 확인하세요.",
                            )
                        }
                        if (found != null) {
                            reservation = found
                            return@searchTrains true
                        }
                        emit(BookingEvent.Status("예약되지 않은 것을 확인했습니다(${errorText(e)}). 계속 확인합니다."))
                    }
                }
                false
            }
        } catch (e: NeedToLoginException) {
            if (relogin == null) throw IllegalStateException("로그인이 만료되었습니다. 다시 로그인하세요. (자동 로그인을 켜면 자동으로 이어갑니다)")
            if (++relogins > 3) throw IllegalStateException("재로그인이 반복되어 중지했습니다. 다시 로그인하세요.")
            emit(BookingEvent.Status("로그인이 만료되어 자동으로 다시 로그인합니다."))
            if (!relogin()) throw IllegalStateException("자동 재로그인에 실패했습니다. 다시 로그인하세요.")
            continue
        } catch (e: Exception) {
            if (!isTransient(e)) throw e
            if (++failures > maxFailures) throw IllegalStateException("통신 오류가 계속되어 중지했습니다.\n${errorText(e)}")
            val wait = backoffMs(failures)
            emit(BookingEvent.Status("일시적인 오류 · ${wait / 1000}초 후 다시 시도합니다 ($failures/$maxFailures)\n${errorText(e)}"))
            if (stop.await(wait)) return
            continue
        }
        reservation?.let {
            stop.set()
            emit(BookingEvent.Reserved(it))
            return
        }
        failures = 0
        relogins = 0
        if (stop.isSet) return
        emit(BookingEvent.Trains(c, trains))
        val delay = retryDelayMs()
        val scope = if (targets.isEmpty()) "" else "선택 ${targets.size}개 열차 · "
        emit(BookingEvent.Status("${scope}${attempt}회 조회 · 예약 가능한 좌석 없음 · ${"%.1f".format(delay / 1000.0)}초 후 재조회"))
        if (stop.await(delay)) return
    }
}
