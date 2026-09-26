package com.chlansgus.ktx

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.chlansgus.ktx.core.KST
import com.chlansgus.ktx.core.SeatGrade
import com.chlansgus.ktx.core.Train
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        BookingController.init(this)
        enableEdgeToEdge()
        setContent { KtxTheme { MainScreen() } }
    }
}

@Composable
private fun KtxTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

private val ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd")

@Composable
private fun MainScreen() {
    val state by BookingController.state.collectAsStateWithLifecycle()
    val context = LocalContext.current

    // 회원번호/비밀번호는 화면 회전 저장(Bundle)에도 남기지 않습니다.
    var userId by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var departure by rememberSaveable { mutableStateOf("서울") }
    var arrival by rememberSaveable { mutableStateOf("부산") }
    var date by rememberSaveable { mutableStateOf(LocalDate.now(KST).format(ISO)) }
    var start by rememberSaveable { mutableStateOf("06:00") }
    var end by rememberSaveable { mutableStateOf("23:59") }
    var count by rememberSaveable { mutableStateOf("1") }
    var grade by rememberSaveable { mutableStateOf(SeatGrade.GENERAL) }
    var pickDate by remember { mutableStateOf(false) }

    fun form() = SearchForm(departure, arrival, date, start, end, count, grade)
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        BookingController.startAuto(form())
    }
    fun startAuto() {
        val needsPermission = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        if (needsPermission) permission.launch(Manifest.permission.POST_NOTIFICATIONS) else BookingController.startAuto(form())
    }

    val idle = !state.busy
    val ready = state.loggedIn && idle

    Scaffold(modifier = Modifier.fillMaxSize().imePadding()) { inner ->
        LazyColumn(
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = inner.calculateTopPadding() + 16.dp, bottom = inner.calculateBottomPadding() + 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Text("KTX 간편 예약", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Text(
                    "예약만 진행합니다. 결제는 반드시 코레일에서 직접 완료하세요.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            item {
                Section("코레일 로그인") {
                    OutlinedTextField(
                        value = userId, onValueChange = { userId = it }, enabled = idle,
                        label = { Text("회원번호 / 휴대폰번호") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = password, onValueChange = { password = it }, enabled = idle,
                        label = { Text("비밀번호") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Button(enabled = idle, onClick = {
                            val pw = password
                            password = ""
                            BookingController.login(userId, pw)
                        }) { Text("로그인") }
                        Spacer(Modifier.width(12.dp))
                        Text(state.loginState, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }

            item {
                Section("검색조건 · KTX / 성인") {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Field("출발역", departure, idle, Modifier.weight(1f)) { departure = it }
                        TextButton(enabled = idle, onClick = { departure = arrival.also { arrival = departure } }) { Text("⇄") }
                        Field("도착역", arrival, idle, Modifier.weight(1f)) { arrival = it }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Field("날짜 YYYY-MM-DD", date, idle, Modifier.weight(1f), KeyboardType.Number) { date = it }
                        TextButton(enabled = idle, onClick = { pickDate = true }) { Text("달력") }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Field("시작 HH:MM", start, idle, Modifier.weight(1f), KeyboardType.Number) { start = it }
                        Field("종료 HH:MM", end, idle, Modifier.weight(1f), KeyboardType.Number) { end = it }
                        Field("인원(성인)", count, idle, Modifier.weight(0.8f), KeyboardType.Number) { count = it }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SeatGrade.entries.forEach { g ->
                            FilterChip(selected = grade == g, enabled = idle, onClick = { grade = g }, label = { Text(g.label) })
                        }
                    }
                }
            }

            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(enabled = ready, onClick = { BookingController.search(form()) }, modifier = Modifier.weight(1f)) { Text("열차 조회") }
                        OutlinedButton(enabled = ready, onClick = { BookingController.reserveSelected(form()) }, modifier = Modifier.weight(1f)) { Text("선택 열차 예약") }
                    }
                    if (state.busy && state.auto) {
                        Button(
                            enabled = !state.stopRequested,
                            onClick = BookingController::cancel,
                            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("자동예약 중지") }
                    } else {
                        Button(enabled = ready, onClick = ::startAuto, modifier = Modifier.fillMaxWidth()) { Text("자동예약 시작") }
                    }
                    Text(
                        "자동예약은 검색조건에 맞는 모든 열차 중 좌석이 있는 첫 열차를 예약합니다. 진행 중에는 다른 앱을 써도 계속됩니다.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            item {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(state.status, fontWeight = FontWeight.SemiBold)
                        if (state.detail.isNotEmpty()) {
                            Spacer(Modifier.padding(top = 4.dp))
                            Text(state.detail, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }

            if (state.trains.isNotEmpty()) {
                item { Text("열차 목록 · 좌석 상태는 예약 확정이 아닙니다", style = MaterialTheme.typography.titleSmall) }
            }
            itemsIndexed(state.trains, key = { _, t -> t.trainNo + t.depDate + t.depTime }) { i, train ->
                TrainRow(train, selected = state.selected == i, enabled = idle) { BookingController.select(i) }
            }
        }
    }

    if (pickDate) DatePick(date, onDismiss = { pickDate = false }) { date = it; pickDate = false }

    state.message?.let { m ->
        AlertDialog(
            onDismissRequest = BookingController::dismissMessage,
            confirmButton = { TextButton(onClick = BookingController::dismissMessage) { Text("확인") } },
            title = { Text(m.title) },
            text = { Text(m.text) },
        )
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            content()
        }
    }
}

@Composable
private fun Field(
    label: String, value: String, enabled: Boolean, modifier: Modifier = Modifier,
    keyboard: KeyboardType = KeyboardType.Text, onChange: (String) -> Unit,
) = OutlinedTextField(
    value = value, onValueChange = onChange, enabled = enabled, singleLine = true,
    label = { Text(label, maxLines = 1) },
    keyboardOptions = KeyboardOptions(keyboardType = keyboard),
    modifier = modifier,
)

@Composable
private fun TrainRow(train: Train, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    fun seat(code: String, has: Boolean) = when {
        has -> "가능"
        code == "00" -> "없음"
        else -> "매진"
    }
    Card(
        modifier = Modifier.fillMaxWidth().clickable(enabled = enabled, onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Row(Modifier.padding(horizontal = 4.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            RadioButton(selected = selected, enabled = enabled, onClick = onClick)
            Column(Modifier.weight(1f)) {
                Text("${train.depHhmm()} → ${train.arrHhmm()}", fontWeight = FontWeight.Bold)
                Text("${train.trainTypeName} ${train.trainNo} · ${train.depName}~${train.arrName}", style = MaterialTheme.typography.bodySmall)
            }
            Column(horizontalAlignment = Alignment.End, modifier = Modifier.padding(end = 8.dp)) {
                Text("일반실 ${seat(train.generalSeat, train.hasGeneralSeat())}", style = MaterialTheme.typography.bodySmall,
                    color = if (train.hasGeneralSeat()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                Text("특실 ${seat(train.specialSeat, train.hasSpecialSeat())}", style = MaterialTheme.typography.bodySmall,
                    color = if (train.hasSpecialSeat()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DatePick(current: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val today = LocalDate.now(KST)
    val initial = runCatching { LocalDate.parse(current.replace("-", ""), DateTimeFormatter.BASIC_ISO_DATE) }.getOrDefault(today)
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
