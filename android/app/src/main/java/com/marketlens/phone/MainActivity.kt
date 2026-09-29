package com.marketlens.phone

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.inputmethod.EditorInfo
import android.webkit.CookieManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/**
 * MarketLens on the phone: the PC computes everything (prices, live judgements, the 토스증권 account); this app shows
 * the PC's screens on the home Wi-Fi. First launch asks for the PC's address (shown on the PC under 설정 → 폰 연결);
 * the PC then asks for its 6-digit code once. Pages of any other address open in the browser, never in the app.
 */
class MainActivity : Activity() {
    private var web: WebView? = null
    private var pc: PcAddress? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pc = PcAddress.parse(prefs().getString(KEY, "") ?: "")
        if (pc == null) showSetup(null) else showWeb()
    }

    private fun prefs() = getSharedPreferences("marketlens", MODE_PRIVATE)

    // ------------------------------------------------------------------ the PC's address
    private fun showSetup(message: String?) {
        web?.destroy()
        web = null
        val pad = dp(24)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, dp(64), pad, pad)
            setBackgroundColor(Color.WHITE)
        }
        box.addView(TextView(this).apply {
            text = "MarketLens"
            textSize = 28f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#0F172A"))
        })
        box.addView(TextView(this).apply {
            text = "PC의 MarketLens에서 설정 → 폰 연결을 켜고, 거기 나온 주소를 입력하세요.\n(같은 와이파이에서만 연결됩니다)"
            textSize = 15f
            setTextColor(Color.parseColor("#475569"))
            setPadding(0, dp(12), 0, dp(20))
        })
        val input = EditText(this).apply {
            hint = "예: 192.168.0.10:8766"
            textSize = 20f
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_GO
            setSingleLine()
            setText(prefs().getString(KEY, "") ?: "")
        }
        box.addView(input, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        val error = TextView(this).apply {
            setTextColor(Color.parseColor("#DC2626"))
            textSize = 14f
            setPadding(0, dp(8), 0, 0)
            text = message ?: ""
            visibility = if (message == null) View.GONE else View.VISIBLE
        }
        box.addView(error)
        val connect = Button(this).apply { text = "연결" }
        box.addView(connect, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(16) })
        val go = {
            val a = PcAddress.parse(input.text.toString())
            if (a == null) {
                error.text = "집 네트워크 주소가 아닙니다. PC 화면의 주소(192.168… 또는 10…)를 그대로 입력하세요."
                error.visibility = View.VISIBLE
            } else {
                prefs().edit().putString(KEY, "${a.host}:${a.port}").apply()
                pc = a
                showWeb()
            }
        }
        connect.setOnClickListener { go() }
        input.setOnEditorActionListener { _, id, _ -> if (id == EditorInfo.IME_ACTION_GO) { go(); true } else false }
        setContentView(box)
    }

    // ------------------------------------------------------------------ the PC's screens
    @SuppressLint("SetJavaScriptEnabled")
    private fun showWeb() {
        val a = pc ?: return showSetup(null)
        CookieManager.getInstance().setAcceptCookie(true)  // the paired device's cookie (HttpOnly, set by the PC)
        val w = WebView(this)
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        w.settings.allowFileAccess = false
        w.settings.allowContentAccess = false
        w.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                if (a.owns(url)) return false
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }  // anything else: the browser
                return true
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) showOffline()
            }
        }
        web = w
        setContentView(w)
        w.loadUrl(a.base + "/")
    }

    private fun showOffline() {
        val a = pc
        web?.destroy()
        web = null
        val pad = dp(24)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(pad, pad, pad, pad)
            setBackgroundColor(Color.WHITE)
        }
        box.addView(TextView(this).apply {
            text = "PC에 연결할 수 없습니다"
            textSize = 22f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#0F172A"))
        })
        box.addView(TextView(this).apply {
            text = "• PC에서 MarketLens가 켜져 있는지\n• 설정 → 폰 연결이 켜져 있는지\n• 폰이 PC와 같은 와이파이인지\n확인하세요." +
                (a?.let { "\n\n주소: ${it.host}:${it.port}" } ?: "")
            textSize = 15f
            setTextColor(Color.parseColor("#475569"))
            setPadding(0, dp(12), 0, dp(20))
        })
        box.addView(Button(this).apply { text = "다시 연결"; setOnClickListener { showWeb() } }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        box.addView(Button(this).apply { text = "주소 바꾸기"; setOnClickListener { showSetup(null) } },
            LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) })
        setContentView(box)
    }

    @Deprecated("Activity.onBackPressed: the page history first")
    override fun onBackPressed() {
        val w = web
        if (w != null && w.canGoBack()) w.goBack() else @Suppress("DEPRECATION") super.onBackPressed()
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()  // keep the pairing across restarts
    }

    override fun onDestroy() {
        web?.destroy()
        super.onDestroy()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val KEY = "pc_address"
    }
}
