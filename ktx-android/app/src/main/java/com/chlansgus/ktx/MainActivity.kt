@file:OptIn(ExperimentalMaterial3Api::class)

package com.chlansgus.ktx

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RangeSlider
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.chlansgus.ktx.core.KST
import com.chlansgus.ktx.core.Reservation
import com.chlansgus.ktx.core.SeatGrade
import com.chlansgus.ktx.core.Train
import com.chlansgus.ktx.core.key
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle as DayStyle
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        BookingController.init(this)
        enableEdgeToEdge()
        setContent { KtxTheme { MainScreen() } }
    }
}

// ───────────────────────── 테마 ─────────────────────────

private val Blue = Color(0xFF3182F6)
private val Red = Color(0xFFF04452)
private val Green = Color(0xFF15B374)

@Composable
private fun KtxTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme()) {
        darkColorScheme(
            primary = Blue, onPrimary = Color.White,
            primaryContainer = Color(0xFF1B2B45), onPrimaryContainer = Color(0xFF90C2FF),
            background = Color(0xFF101012), surface = Color(0xFF1C1C1E), onSurface = Color(0xFFF2F2F4),
            surfaceVariant = Color(0xFF2C2C30), onSurfaceVariant = Color(0xFF9E9EA6),
            outline = Color(0xFF3A3A3F), error = Red,
        )
    } else {
        lightColorScheme(
            primary = Blue, onPrimary = Color.White,
            primaryContainer = Color(0xFFE8F3FF), onPrimaryContainer = Color(0xFF1B64DA),
            background = Color(0xFFF2F4F6), surface = Color.White, onSurface = Color(0xFF191F28),
            surfaceVariant = Color(0xFFF2F4F6), onSurfaceVariant = Color(0xFF6B7684),
            outline = Color(0xFFE5E8EB), error = Red,
        )
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

private val sub: Color @Composable get() = MaterialTheme.colorScheme.onSurfaceVariant
private val ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd")

private val STATIONS = listOf(
    "서울", "용산", "영등포", "광명", "수원", "행신", "청량리", "상봉",
    "천안아산", "오송", "대전", "서대전", "김천구미", "구미", "동대구", "대구",
    "신경주", "경주", "울산(통도사)", "포항", "밀양", "물금", "구포", "부산",
    "창원중앙", "창원", "마산", "진주", "익산", "정읍", "광주송정", "나주",
    "목포", "전주", "남원", "순천", "여천", "여수EXPO", "강릉", "정동진",
    "동해", "평창", "진부(오대산)", "둔내", "횡성", "만종", "원주", "서원주",
    "안동", "영주", "제천", "단양", "공주", "계룡", "논산",
)

// ───────────────────────── 메인 화면 ─────────────────────────

@Composable
private fun MainScreen() {
    val state by BookingController.state.collectAsStateWithLifecycle()
    val context = LocalContext.current

    var departure by rememberSaveable { mutableStateOf("서울") }
    var arrival by rememberSaveable { mutableStateOf("부산") }
    var date by rememberSaveable { mutableStateOf(LocalDate.now(KST).format(ISO)) }
    var startHour by rememberSaveable { mutableStateOf(6f) }
    var endHour by rememberSaveable { mutableStateOf(24f) }
    var count by rememberSaveable { mutableStateOf(1) }
    var grade by rememberSaveable { mutableStateOf(SeatGrade.GENERAL) }
    var picking by remember { mutableStateOf<String?>(null) } // "출발" / "도착"
    var pickDate by remember { mutableStateOf(false) }

    fun form() = SearchForm(
        departure, arrival, date,
        "%02d:00".format(startHour.toInt()),
        if (endHour >= 24f) "23:59" else "%02d:00".format(endHour.toInt()),
        count.toString(), grade,
    )

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        BookingController.startAuto(form())
    }
    fun startAuto() {
        val needs = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        if (needs) permission.launch(Manifest.permission.POST_NOTIFICATIONS) else BookingController.startAuto(form())
    }

    val idle = !state.busy
    Box(
        Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .imePadding(),
    ) {
        LazyColumn(
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 56.dp, bottom = 140.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Header(state) }

            if (!state.loggedIn) {
                item { if (state.loggingIn) LoggingInCard() else LoginCard(state) }
            }

            item {
                RouteCard(departure, arrival, idle, onPick = { picking = it }) {
                    departure = arrival.also { arrival = departure }
                }
            }
            item { DateCard(date, idle, onPick = { date = it }, onCalendar = { pickDate = true }) }
            item {
                TimeCard(startHour, endHour, idle) { s, e -> startHour = s; endHour = e }
            }
            item { OptionCard(count, grade, idle, onCount = { count = it }, onGrade = { grade = it }) }

            item {
                AnimatedVisibility(visible = state.auto) { AutoProgressCard(state) }
                if (!state.auto && state.status.isNotEmpty() && state.loggedIn) StatusLine(state.status)
            }

            if (state.trains.isNotEmpty()) {
                item { TrainHeader(state, idle) }
                items(state.trains, key = { it.key }) { train ->
                    TrainCard(train, grade, selected = train.key in state.selected, enabled = idle) {
                        BookingController.toggle(train.key)
                    }
                }
            }
        }

        if (state.loggedIn) {
            BottomBar(
                state,
                modifier = Modifier.align(Alignment.BottomCenter),
                onSearch = { BookingController.search(form()) },
                onReserve = { BookingController.reserveSelected(form()) },
                onAuto = ::startAuto,
            )
        }
    }

    picking?.let { which ->
        StationSheet(
            title = "${which}역 선택",
            onDismiss = { picking = null },
        ) { station ->
            if (which == "출발") departure = station else arrival = station
            picking = null
        }
    }
    if (pickDate) DatePick(date, onDismiss = { pickDate = false }) { date = it; pickDate = false }

    state.message?.let { m ->
        AlertDialog(
            onDismissRequest = BookingController::dismissMessage,
            confirmButton = { TextButton(onClick = BookingController::dismissMessage) { Text("확인", fontWeight = FontWeight.Bold) } },
            title = { Text(m.title, fontWeight = FontWeight.Bold) },
            text = { Text(m.text) },
            shape = RoundedCornerShape(24.dp),
        )
    }
    state.reservation?.let { r -> SuccessSheet(r, onDismiss = BookingController::dismissReservation) }
}

// ───────────────────────── 구성 요소 ─────────────────────────

@Composable
private fun Header(state: UiState) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("KTX 간편예매", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold, fontSize = 15.sp)
        Spacer(Modifier.weight(1f))
        if (state.loggedIn) {
            var menu by remember { mutableStateOf(false) }
            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.surface,
                onClick = { menu = !menu },
            ) {
                Text(
                    "${state.memberName ?: "회원"}님 ${if (state.autoLogin) "· 자동" else ""} ▾",
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                    fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                )
            }
            if (menu) {
                AlertDialog(
                    onDismissRequest = { menu = false },
                    title = { Text("로그아웃할까요?", fontWeight = FontWeight.Bold) },
                    text = { Text("자동 로그인 정보도 함께 삭제됩니다.") },
                    confirmButton = {
                        TextButton(enabled = !state.busy, onClick = { menu = false; BookingController.logout() }) {
                            Text("로그아웃", color = Red, fontWeight = FontWeight.Bold)
                        }
                    },
                    dismissButton = { TextButton(onClick = { menu = false }) { Text("취소") } },
                    shape = RoundedCornerShape(24.dp),
                )
            }
        }
    }
    Spacer(Modifier.height(12.dp))
    Text(
        if (state.loggedIn) "어디로 떠나시나요?" else "로그인하고\n빠르게 예매하세요",
        fontSize = 26.sp, fontWeight = FontWeight.Bold, lineHeight = 34.sp,
    )
    Spacer(Modifier.height(4.dp))
    Text("예매만 도와드려요. 결제는 코레일톡에서 직접 해주세요.", color = sub, fontSize = 14.sp)
    Spacer(Modifier.height(4.dp))
}

@Composable
private fun CardBox(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface, modifier = modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { content() }
    }
}

@Composable
private fun CardTitle(text: String, trailing: String? = null) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(text, color = sub, fontSize = 14.sp, fontWeight = FontWeight.Medium)
        Spacer(Modifier.weight(1f))
        trailing?.let { Text(it, fontSize = 15.sp, fontWeight = FontWeight.Bold) }
    }
}

@Composable
private fun TossField(
    value: String, onChange: (String) -> Unit, placeholder: String,
    password: Boolean = false, keyboard: KeyboardType = KeyboardType.Text,
) {
    var visible by remember { mutableStateOf(false) }
    OutlinedTextField(
        value = value, onValueChange = onChange, singleLine = true,
        placeholder = { Text(placeholder, color = sub) },
        visualTransformation = if (password && !visible) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = if (password) KeyboardType.Password else keyboard),
        trailingIcon = if (password) {
            { TextButton(onClick = { visible = !visible }) { Text(if (visible) "숨기기" else "보기", fontSize = 13.sp) } }
        } else null,
        shape = RoundedCornerShape(14.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
            unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
            unfocusedBorderColor = Color.Transparent,
            focusedBorderColor = MaterialTheme.colorScheme.primary,
        ),
        textStyle = TextStyle(fontSize = 17.sp),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun BigButton(
    text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true,
    container: Color = MaterialTheme.colorScheme.primary, content: Color = Color.White, loading: Boolean = false,
) {
    Button(
        onClick = onClick, enabled = enabled && !loading, modifier = modifier.height(56.dp),
        shape = RoundedCornerShape(16.dp),
        colors = ButtonDefaults.buttonColors(containerColor = container, contentColor = content),
        contentPadding = PaddingValues(horizontal = 12.dp),
    ) {
        if (loading) {
            CircularProgressIndicator(Modifier.size(20.dp), color = content, strokeWidth = 2.dp)
            Spacer(Modifier.width(8.dp))
        }
        Text(text, fontSize = 17.sp, fontWeight = FontWeight.Bold, maxLines = 1)
    }
}

@Composable
private fun LoginCard(state: UiState) {
    // 비밀번호는 화면 회전 저장(Bundle)에도 남기지 않습니다.
    var userId by remember { mutableStateOf(state.savedUserId) }
    var password by remember { mutableStateOf("") }
    var keepLogin by remember { mutableStateOf(true) }
    CardBox {
        TossField(userId, { userId = it }, "회원번호 또는 휴대폰번호", keyboard = KeyboardType.Phone)
        TossField(password, { password = it }, "코레일 비밀번호", password = true)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("자동 로그인", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                Text("비밀번호는 휴대폰 보안영역에 암호화돼 저장돼요", color = sub, fontSize = 12.sp)
            }
            Switch(checked = keepLogin, onCheckedChange = { keepLogin = it })
        }
        BigButton("로그인", modifier = Modifier.fillMaxWidth(), enabled = !state.busy, onClick = {
            val pw = password
            password = ""
            BookingController.login(userId, pw, keepLogin)
        })
    }
}

@Composable
private fun LoggingInCard() {
    CardBox {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.5.dp)
            Spacer(Modifier.width(12.dp))
            Text("로그인하고 있어요…", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
private fun RouteCard(dep: String, arr: String, enabled: Boolean, onPick: (String) -> Unit, onSwap: () -> Unit) {
    CardBox {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StationBox("출발", dep, enabled, Modifier.weight(1f)) { onPick("출발") }
            Surface(
                shape = CircleShape, color = MaterialTheme.colorScheme.surfaceVariant,
                onClick = onSwap, enabled = enabled, modifier = Modifier.size(44.dp),
            ) {
                Box(contentAlignment = Alignment.Center) { Text("⇄", fontSize = 20.sp, color = MaterialTheme.colorScheme.primary) }
            }
            StationBox("도착", arr, enabled, Modifier.weight(1f)) { onPick("도착") }
        }
    }
}

@Composable
private fun StationBox(label: String, name: String, enabled: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Column(
        modifier.clickable(enabled = enabled, onClick = onClick).padding(vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(label, color = sub, fontSize = 13.sp)
        Text(name, fontSize = 26.sp, fontWeight = FontWeight.Bold, maxLines = 1, textAlign = TextAlign.Center)
    }
}

@Composable
private fun DateCard(date: String, enabled: Boolean, onPick: (String) -> Unit, onCalendar: () -> Unit) {
    val today = LocalDate.now(KST)
    val selected = runCatching { LocalDate.parse(date, ISO) }.getOrDefault(today)
    val week = selected.dayOfWeek.getDisplayName(DayStyle.SHORT, Locale.KOREAN)
    CardBox {
        CardTitle("가는 날", "${selected.monthValue}월 ${selected.dayOfMonth}일 ($week)")
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items((0L..30L).map { today.plusDays(it) }) { d ->
                val on = d == selected
                val dow = d.dayOfWeek.value // 6=토, 7=일
                val bg by animateColorAsState(if (on) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant, label = "day")
                val fg = when {
                    on -> Color.White
                    dow == 7 -> Red
                    dow == 6 -> Blue
                    else -> MaterialTheme.colorScheme.onSurface
                }
                Surface(
                    shape = RoundedCornerShape(14.dp), color = bg, enabled = enabled,
                    onClick = { onPick(d.format(ISO)) }, modifier = Modifier.width(56.dp),
                ) {
                    Column(Modifier.padding(vertical = 10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            when (d) { today -> "오늘"; today.plusDays(1) -> "내일"; else -> d.dayOfWeek.getDisplayName(DayStyle.SHORT, Locale.KOREAN) },
                            color = fg.copy(alpha = if (on) 0.9f else 0.8f), fontSize = 12.sp,
                        )
                        Text("${d.dayOfMonth}", color = fg, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
            item {
                Surface(
                    shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surfaceVariant,
                    enabled = enabled, onClick = onCalendar, modifier = Modifier.width(56.dp),
                ) {
                    Column(Modifier.padding(vertical = 10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("달력", fontSize = 12.sp, color = sub)
                        Text("📅", fontSize = 18.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun TimeCard(start: Float, end: Float, enabled: Boolean, onChange: (Float, Float) -> Unit) {
    CardBox {
        CardTitle("출발 시간대", "${start.toInt()}시 ~ ${if (end >= 24f) "24시" else "${end.toInt()}시"}")
        RangeSlider(
            value = start..end, enabled = enabled,
            onValueChange = { r -> if (r.endInclusive - r.start >= 1f) onChange(r.start, r.endInclusive) },
            valueRange = 0f..24f, steps = 23,
        )
        Row {
            listOf("새벽" to (0f to 6f), "오전" to (6f to 12f), "오후" to (12f to 18f), "저녁" to (18f to 24f), "종일" to (0f to 24f)).forEach { (label, r) ->
                val on = start == r.first && end == r.second
                Surface(
                    shape = CircleShape, enabled = enabled,
                    color = if (on) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
                    onClick = { onChange(r.first, r.second) },
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        label, textAlign = TextAlign.Center, fontSize = 13.sp,
                        color = if (on) MaterialTheme.colorScheme.onPrimaryContainer else sub,
                        fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
                        modifier = Modifier.padding(vertical = 6.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun OptionCard(count: Int, grade: SeatGrade, enabled: Boolean, onCount: (Int) -> Unit, onGrade: (SeatGrade) -> Unit) {
    CardBox {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("인원", color = sub, fontSize = 14.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
            Text("성인", fontSize = 15.sp, modifier = Modifier.padding(end = 12.dp))
            Stepper("−", enabled && count > 1) { onCount(count - 1) }
            Text("$count", fontSize = 18.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.width(40.dp))
            Stepper("+", enabled && count < 9) { onCount(count + 1) }
        }
        Text("좌석", color = sub, fontSize = 14.sp, fontWeight = FontWeight.Medium)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            val labels = mapOf(SeatGrade.GENERAL to "일반실", SeatGrade.SPECIAL to "특실", SeatGrade.ANY to "상관없음")
            SeatGrade.entries.forEachIndexed { i, g ->
                SegmentedButton(
                    selected = grade == g, enabled = enabled, onClick = { onGrade(g) },
                    shape = SegmentedButtonDefaults.itemShape(i, SeatGrade.entries.size),
                    icon = {},
                ) { Text(labels.getValue(g), fontWeight = if (grade == g) FontWeight.Bold else FontWeight.Normal) }
            }
        }
        if (grade == SeatGrade.ANY) Text("일반실을 먼저 노리고, 없으면 특실로 잡아요", color = sub, fontSize = 12.sp)
    }
}

@Composable
private fun Stepper(symbol: String, enabled: Boolean, onClick: () -> Unit) {
    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceVariant, enabled = enabled, onClick = onClick, modifier = Modifier.size(36.dp)) {
        Box(contentAlignment = Alignment.Center) {
            Text(symbol, fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.alpha(if (enabled) 1f else 0.3f))
        }
    }
}

@Composable
private fun StatusLine(text: String) {
    Text(text, color = sub, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 4.dp))
}

@Composable
private fun AutoProgressCard(state: UiState) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(state.autoStartedAt) {
        while (true) { now = System.currentTimeMillis(); delay(1000) }
    }
    val elapsed = ((now - state.autoStartedAt) / 1000).coerceAtLeast(0)
    val pulse = rememberInfiniteTransition(label = "pulse")
    val scale by pulse.animateFloat(1f, 1.8f, infiniteRepeatable(tween(1100), RepeatMode.Restart), label = "s")
    val fade by pulse.animateFloat(0.6f, 0f, infiniteRepeatable(tween(1100), RepeatMode.Restart), label = "a")
    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.primaryContainer, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(contentAlignment = Alignment.Center, modifier = Modifier.size(24.dp)) {
                    Box(Modifier.size(12.dp).scale(scale).alpha(fade).background(Blue, CircleShape))
                    Box(Modifier.size(12.dp).background(Blue, CircleShape))
                }
                Spacer(Modifier.width(10.dp))
                Text(
                    if (state.stopRequested) "멈추는 중이에요" else "빈자리를 찾고 있어요",
                    fontSize = 18.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                Stat("확인 횟수", "${state.attempts}회")
                Stat("경과 시간", "%d:%02d".format(elapsed / 60, elapsed % 60))
                Stat("대상", if (state.selected.isEmpty()) "시간대 전체" else "${state.selected.size}개 열차")
            }
            Text(state.status, fontSize = 13.sp, color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.8f))
            Text("다른 앱을 쓰거나 화면을 꺼도 계속 찾아요. 잡히면 알림으로 알려드릴게요.", fontSize = 12.sp, color = sub)
        }
    }
}

@Composable
private fun Stat(label: String, value: String) {
    Column {
        Text(label, fontSize = 12.sp, color = sub)
        Text(value, fontSize = 17.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun TrainHeader(state: UiState, idle: Boolean) {
    Column(Modifier.padding(top = 8.dp, start = 4.dp, end = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("열차 ${state.trains.size}개", fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.weight(1f))
            if (state.selected.isNotEmpty()) {
                TextButton(enabled = idle, onClick = BookingController::clearSelection) { Text("선택 해제 (${state.selected.size})") }
            }
        }
        Text("원하는 열차를 여러 개 고르면 그 열차만 집중해서 노려요", color = sub, fontSize = 13.sp)
    }
}

private fun duration(train: Train): String {
    val f = DateTimeFormatter.ofPattern("HHmmss")
    return runCatching {
        var m = (LocalTime.parse(train.arrTime, f).toSecondOfDay() - LocalTime.parse(train.depTime, f).toSecondOfDay()) / 60
        if (m < 0) m += 24 * 60
        if (m >= 60) "${m / 60}시간 ${m % 60}분" else "${m}분"
    }.getOrDefault("")
}

@Composable
private fun TrainCard(train: Train, grade: SeatGrade, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val border by animateColorAsState(if (selected) MaterialTheme.colorScheme.primary else Color.Transparent, label = "border")
    Surface(
        shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(2.dp, border), onClick = onClick, enabled = enabled,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(horizontal = 18.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(train.depHhmm(), fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    Text("  →  ", color = sub)
                    Text(train.arrHhmm(), fontSize = 20.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.75f))
                }
                Spacer(Modifier.height(2.dp))
                Text("${duration(train)} · ${train.trainTypeName} ${train.trainNo.trimStart('0')}", color = sub, fontSize = 13.sp)
            }
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                SeatBadge("일반", train.generalSeat, train.hasGeneralSeat())
                SeatBadge("특실", train.specialSeat, train.hasSpecialSeat())
            }
            Spacer(Modifier.width(12.dp))
            Box(
                Modifier.size(24.dp).background(if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Text("✓", color = if (selected) Color.White else sub.copy(alpha = 0.5f), fontSize = 13.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun SeatBadge(label: String, code: String, has: Boolean) {
    val (text, fg, bg) = when {
        has -> Triple("$label 가능", Green, Green.copy(alpha = 0.12f))
        code == "00" -> Triple("$label 없음", sub.copy(alpha = 0.6f), Color.Transparent)
        else -> Triple("$label 매진", sub, MaterialTheme.colorScheme.surfaceVariant)
    }
    Text(
        text, color = fg, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.background(bg, RoundedCornerShape(8.dp)).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

@Composable
private fun BottomBar(state: UiState, modifier: Modifier, onSearch: () -> Unit, onReserve: () -> Unit, onAuto: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shadowElevation = 12.dp, modifier = modifier.fillMaxWidth()) {
        Row(
            Modifier.navigationBarsPadding().padding(horizontal = 20.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            val tonal = MaterialTheme.colorScheme.primaryContainer
            val onTonal = MaterialTheme.colorScheme.onPrimaryContainer
            when {
                state.auto -> BigButton(
                    if (state.stopRequested) "멈추는 중…" else "자동예매 멈추기",
                    onClick = BookingController::cancel, enabled = !state.stopRequested,
                    container = Red, modifier = Modifier.fillMaxWidth(),
                )
                state.busy -> BigButton(state.status.removeSuffix("…"), onClick = {}, loading = true, modifier = Modifier.fillMaxWidth())
                state.trains.isEmpty() -> BigButton("열차 조회하기", onClick = onSearch, modifier = Modifier.fillMaxWidth())
                else -> {
                    BigButton("조회", onClick = onSearch, container = tonal, content = onTonal, modifier = Modifier.weight(0.8f))
                    if (state.selected.size == 1) {
                        BigButton("바로 예매", onClick = onReserve, container = tonal, content = onTonal, modifier = Modifier.weight(1f))
                    }
                    BigButton(
                        if (state.selected.isEmpty()) "전체 자동예매" else "${state.selected.size}개 자동예매",
                        onClick = onAuto, modifier = Modifier.weight(1.4f),
                    )
                }
            }
        }
    }
}

@Composable
private fun StationSheet(title: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    var query by remember { mutableStateOf("") }
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            TossField(query, { query = it }, "역 이름 검색")
            val q = query.trim()
            val list = STATIONS.filter { q.isEmpty() || it.contains(q) }
            if (q.isNotEmpty() && q !in STATIONS) {
                Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.primaryContainer, onClick = { onPick(q) }, modifier = Modifier.fillMaxWidth()) {
                    Text("'$q' 역으로 입력하기", modifier = Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onPrimaryContainer, fontWeight = FontWeight.SemiBold)
                }
            }
            LazyVerticalGrid(
                columns = GridCells.Fixed(3), modifier = Modifier.heightIn(max = 460.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(list) { name ->
                    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceVariant, onClick = { onPick(name) }) {
                        Text(name, textAlign = TextAlign.Center, fontSize = 15.sp, maxLines = 1, modifier = Modifier.padding(vertical = 14.dp, horizontal = 4.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun SuccessSheet(r: Reservation, onDismiss: () -> Unit) {
    val context = LocalContext.current
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            Modifier.padding(horizontal = 24.dp).padding(bottom = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(Modifier.size(64.dp).background(Blue, CircleShape), contentAlignment = Alignment.Center) {
                Text("✓", color = Color.White, fontSize = 32.sp, fontWeight = FontWeight.Bold)
            }
            Text("예매에 성공했어요!", fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Text("결제해야 승차권이 확정돼요", color = Red, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    InfoRow("예약번호", r.rsvId)
                    if (r.confirmed) {
                        InfoRow("열차", "${r.trainTypeName} ${r.trainNo?.trimStart('0')}")
                        InfoRow("구간", "${r.depName} → ${r.arrName}")
                        InfoRow("시간", "${fmtDate(r.runDate)} ${fmtTime(r.depTime)} → ${fmtTime(r.arrTime)}")
                        r.price?.let { InfoRow("금액", "%,d원 (%d석)".format(it, r.seatCount ?: 0)) }
                        InfoRow("결제기한", "${fmtDate(r.buyLimitDate)} ${fmtTime(r.buyLimitTime)}까지", highlight = true)
                    } else {
                        Text("상세 정보를 불러오지 못했어요. 코레일톡 예약내역에서 확인해주세요.", color = sub, fontSize = 13.sp)
                    }
                }
            }
            BigButton("코레일톡에서 결제하기", onClick = { openKorail(context) }, modifier = Modifier.fillMaxWidth())
            TextButton(onClick = onDismiss) { Text("닫기", color = sub) }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String, highlight: Boolean = false) {
    Row {
        Text(label, color = sub, fontSize = 14.sp, modifier = Modifier.width(72.dp))
        Text(value, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = if (highlight) Red else MaterialTheme.colorScheme.onSurface)
    }
}

private fun fmtTime(t: String?) = if (t != null && t.length >= 4) "${t.substring(0, 2)}:${t.substring(2, 4)}" else ""
private fun fmtDate(d: String?) = if (d != null && d.length == 8) "${d.substring(4, 6).toInt()}월 ${d.substring(6).toInt()}일" else ""

private fun openKorail(context: Context) {
    val pkg = "com.korail.talk"
    val intents = listOfNotNull(
        context.packageManager.getLaunchIntentForPackage(pkg),
        Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$pkg")),
        Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$pkg")),
    )
    for (i in intents) {
        if (runCatching { context.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess) return
    }
}

@Composable
private fun DatePick(current: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val today = LocalDate.now(KST)
    val initial = runCatching { LocalDate.parse(current, ISO) }.getOrDefault(today)
    val state = rememberDatePickerState(
        initialSelectedDateMillis = initial.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
        selectableDates = object : SelectableDates {
            override fun isSelectableDate(utcTimeMillis: Long) =
                !Instant.ofEpochMilli(utcTimeMillis).atZone(ZoneOffset.UTC).toLocalDate().isBefore(today)
        },
    )
    DatePickerDialog(
        onDismissRequest = onDismiss,
        confirmButton = {
            TextButton(onClick = {
                state.selectedDateMillis?.let { onPick(Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate().format(ISO)) } ?: onDismiss()
            }) { Text("확인") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
    ) { DatePicker(state = state) }
}
