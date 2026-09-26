package com.chlansgus.ktx

import android.content.Context
import com.chlansgus.ktx.core.BookingEvent
import com.chlansgus.ktx.core.Conditions
import com.chlansgus.ktx.core.KorailClient
import com.chlansgus.ktx.core.Reservation
import com.chlansgus.ktx.core.SeatGrade
import com.chlansgus.ktx.core.SoldOutException
import com.chlansgus.ktx.core.StopSignal
import com.chlansgus.ktx.core.Train
import com.chlansgus.ktx.core.autoReserve
import com.chlansgus.ktx.core.errorText
import com.chlansgus.ktx.core.findReservation
import com.chlansgus.ktx.core.key
import com.chlansgus.ktx.core.reserveTrain
import com.chlansgus.ktx.core.searchTrains
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.Executors

data class Message(val title: String, val text: String, val error: Boolean)

data class UiState(
    val loggedIn: Boolean = false,
    val loggingIn: Boolean = false,
    val memberName: String? = null,
    /** 자동 로그인이 켜져 있고 저장된 계정이 있음 */
    val autoLogin: Boolean = false,
    val savedUserId: String = "",
    val loginState: String = "로그인 전",
    val busy: Boolean = false,
    val auto: Boolean = false,
    val stopRequested: Boolean = false,
    val trains: List<Train> = emptyList(),
    val searched: Conditions? = null,
    /** 선택한 열차 키. 1개면 "선택 열차 예약", 여러 개면 자동예약 대상. */
    val selected: Set<String> = emptySet(),
    val status: String = "로그인 후 검색조건을 입력하세요.",
    val detail: String = "",
    val message: Message? = null,
    /** 자동예약 진행 표시용 */
    val attempts: Int = 0,
    val autoStartedAt: Long = 0L,
    /** 예약 성공 결과 (성공 화면 표시용) */
    val reservation: Reservation? = null,
)

data class SearchForm(
    val departure: String,
    val arrival: String,
    val date: String,
    val start: String,
    val end: String,
    val count: String,
    val grade: SeatGrade,
) {
    fun conditions() = Conditions.parse(departure, arrival, date, start, end, count, grade)
}

/**
 * 로그인 세션과 작업 상태는 프로세스 안에서만 보관합니다.
 * 계정 정보는 사용자가 자동 로그인을 켠 경우에만 [CredentialStore]에 암호화해 저장합니다.
 * 작업은 한 번에 하나씩 백그라운드 스레드에서 실행하고, 결과는 [state]로 화면에 전달합니다.
 */
object BookingController {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private val worker = Executors.newSingleThreadExecutor { Thread(it, "korail-worker") }
    @Volatile private var client: KorailClient? = null
    @Volatile private var stop = StopSignal()
    private lateinit var app: Context
    private lateinit var store: CredentialStore

    fun init(context: Context) {
        if (::app.isInitialized) return
        app = context.applicationContext
        store = CredentialStore(app)
        val saved = store.load()
        _state.update { it.copy(autoLogin = saved != null, savedUserId = saved?.userId.orEmpty()) }
        if (saved != null) login(saved.userId, saved.password, remember = true, automatic = true)
    }

    /** 세션을 끝내고 자동 로그인 정보도 지웁니다. */
    fun logout() {
        if (_state.value.busy) return
        client?.close()
        client = null
        store.clear()
        _state.update {
            UiState(status = "로그아웃했습니다.")
        }
    }

    fun login(userId: String, password: String, remember: Boolean, automatic: Boolean = false) {
        val digits = userId.trim().replace("-", "")
        val user = if (Regex("01\\d\\d{7,8}").matches(digits)) {
            "${digits.take(3)}-${digits.substring(3, digits.length - 4)}-${digits.takeLast(4)}"
        } else userId.trim()
        if (user.isEmpty() || password.isEmpty()) {
            return invalid("회원번호 또는 휴대폰번호와 비밀번호를 입력하세요.")
        }
        if (_state.value.busy) return
        client?.close()
        client = null
        _state.update { it.copy(loggedIn = false, loggingIn = true, loginState = if (automatic) "자동 로그인 중" else "로그인 중") }
        launch("로그인") {
            val c = KorailClient()
            try {
                if (!c.login(user, password)) {
                    throw IllegalStateException(c.serverError.ifEmpty { "로그인 거절: 회원번호/휴대폰번호와 비밀번호를 확인하세요." })
                }
            } catch (e: Exception) {
                c.close()
                _state.update { it.copy(loggingIn = false) }
                // 저장된 비밀번호가 거절되면(비밀번호 변경 등) 자동 로그인을 끕니다. 통신 오류일 때는 유지합니다.
                if (automatic && e is IllegalStateException) {
                    store.clear()
                    _state.update { it.copy(autoLogin = false) }
                }
                throw e
            }
            client = c
            if (remember) runCatching { store.save(user, password) } else store.clear()
            _state.update {
                it.copy(
                    loggedIn = true,
                    loggingIn = false,
                    memberName = c.memberName,
                    autoLogin = remember,
                    savedUserId = if (remember) user else "",
                    loginState = "로그인됨",
                    status = "로그인했습니다. 열차를 조회하거나 자동예매를 시작하세요.",
                )
            }
        }
    }

    fun search(form: SearchForm) {
        val conditions = parse(form) ?: return
        val c = client ?: return
        launch("열차 조회") {
            val trains = searchTrains(c, conditions, stop)
            showTrains(conditions, trains)
            status("조회 완료 · ${trains.size}개 열차 · 예약할 열차를 선택하세요.")
        }
    }

    fun toggle(key: String) = _state.update {
        if (it.busy) it else it.copy(selected = if (key in it.selected) it.selected - key else it.selected + key)
    }

    fun clearSelection() = _state.update { if (it.busy) it else it.copy(selected = emptySet()) }

    fun reserveSelected(form: SearchForm) {
        val conditions = parse(form) ?: return
        val c = client ?: return
        val s = _state.value
        if (conditions != s.searched) return invalid("검색조건이 변경되었습니다. 열차 조회를 다시 실행하세요.")
        if (s.selected.size > 1) return invalid("바로 예약은 열차 1개만 선택하세요. 여러 열차는 자동예약으로 노릴 수 있습니다.")
        val train = s.trains.firstOrNull { it.key in s.selected } ?: return invalid("목록에서 예약할 열차를 선택하세요.")
        launch("예약") {
            val reservation = try {
                reserveTrain(c, train, conditions)
            } catch (e: SoldOutException) {
                throw IllegalStateException("선택한 좌석이 매진되었거나 요청 인원만큼 남아 있지 않습니다.")
            } catch (e: Exception) {
                // 응답이 불확실하면 예약내역에서 실제 결과를 확인합니다.
                status("예약 응답 확인 중… 예약내역을 조회합니다.")
                val found = runCatching { Thread.sleep(2000); findReservation(c, train) }
                found.getOrNull()?.let { return@launch reserved(it) }
                if (found.isSuccess) throw IllegalStateException("예약되지 않았습니다.\n${errorText(e)}")
                throw IllegalStateException("${errorText(e)}\n예약내역도 확인하지 못했습니다. 재시도 전 코레일 예약내역을 확인하세요.")
            }
            reserved(reservation)
        }
    }

    fun startAuto(form: SearchForm) {
        val conditions = parse(form) ?: return
        val c = client ?: return
        val s = _state.value
        val targets = s.trains.map { it.key }.filter { it in s.selected }.toSet()
        if (targets.isNotEmpty() && conditions != s.searched) {
            return invalid("검색조건이 변경되었습니다. 열차 조회를 다시 하거나 선택을 해제하세요.")
        }
        launch("자동예약", auto = true) {
            // 자동 로그인이 켜져 있으면 세션 만료 시 저장된 계정으로 다시 로그인합니다.
            val relogin: (() -> Boolean)? = if (store.autoLogin) {
                { store.load()?.let { saved -> c.login(saved.userId, saved.password) } ?: false }
            } else null
            autoReserve(c, conditions, stop, targets = targets, relogin = relogin, emit = { event ->
                when (event) {
                    is BookingEvent.Trains -> showTrains(event.conditions, event.trains)
                    is BookingEvent.Status -> status(event.text)
                    is BookingEvent.Reserved -> reserved(event.reservation)
                }
            })
        }
    }

    fun cancel() {
        val s = _state.value
        if (!s.busy || !s.auto) return
        stop.set()
        _state.update { it.copy(stopRequested = true, status = "중지 요청됨 · 진행 중인 요청의 결과를 확인한 뒤 멈춥니다.") }
        AutoReserveService.update(app, "중지 요청됨")
    }

    fun dismissMessage() = _state.update { it.copy(message = null) }

    fun dismissReservation() = _state.update { it.copy(reservation = null) }

    private fun parse(form: SearchForm): Conditions? = try {
        form.conditions()
    } catch (e: IllegalArgumentException) {
        invalid(e.message ?: "입력값을 확인하세요.")
        null
    }

    private fun invalid(text: String) = _state.update {
        it.copy(status = "입력 확인: $text", message = Message("입력 확인", text, error = true))
    }

    private fun launch(title: String, auto: Boolean = false, work: () -> Unit) {
        synchronized(this) {
            if (_state.value.busy) return
            stop = StopSignal()
            _state.update {
                it.copy(
                    busy = true, auto = auto, stopRequested = false, status = "$title 중…",
                    attempts = if (auto) 0 else it.attempts,
                    autoStartedAt = if (auto) System.currentTimeMillis() else it.autoStartedAt,
                )
            }
        }
        if (auto) AutoReserveService.start(app)
        worker.execute {
            try {
                work()
            } catch (e: Throwable) {
                val text = errorText(e)
                _state.update {
                    it.copy(
                        loginState = if (title == "로그인") "로그인 실패" else it.loginState,
                        loggingIn = false,
                        status = "$title 실패: $text",
                        detail = "$title 실패\n$text",
                        message = Message("$title 실패", text, error = true),
                    )
                }
                if (auto) AutoReserveService.notifyResult(app, "$title 실패", text)
            } finally {
                _state.update {
                    it.copy(
                        busy = false,
                        auto = false,
                        stopRequested = false,
                        status = if (it.stopRequested && it.status.startsWith("중지 요청됨")) "자동예약이 중지되었습니다." else it.status,
                    )
                }
                if (auto) AutoReserveService.stop()
            }
        }
    }

    private fun showTrains(conditions: Conditions, trains: List<Train>) = _state.update {
        // 선택 열차만 노리는 자동예약은 좁은 범위만 조회하므로, 기존 목록은 두고 받은 열차의 좌석 상태만 갱신합니다.
        val attempts = if (it.auto) it.attempts + 1 else it.attempts
        if (it.auto && it.selected.isNotEmpty() && conditions == it.searched) {
            val fresh = trains.associateBy { t -> t.key }
            return@update it.copy(trains = it.trains.map { t -> fresh[t.key] ?: t }, attempts = attempts)
        }
        val keys = trains.map { t -> t.key }.toSet()
        it.copy(searched = conditions, trains = trains, selected = it.selected.filter { k -> k in keys }.toSet(), attempts = attempts)
    }

    private fun status(text: String) {
        if (stop.isSet) return
        _state.update { it.copy(status = text) }
        if (_state.value.auto) AutoReserveService.update(app, text)
    }

    private fun reserved(r: Reservation) {
        val text = "예약 성공 · 예약번호 ${r.rsvId}\n${r.describe()}\n" +
            "자동조회가 종료되었습니다. 결제 기한 내 코레일 앱/홈페이지에서 직접 결제하세요."
        _state.update {
            it.copy(
                status = "예약 성공 · 결제는 코레일에서 직접 진행하세요.",
                detail = text,
                reservation = r,
            )
        }
        AutoReserveService.notifyResult(app, "KTX 예약 성공 (미결제)", "예약번호 ${r.rsvId} · 결제기한 내 코레일에서 결제하세요.")
    }
}
