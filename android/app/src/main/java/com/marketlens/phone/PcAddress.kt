package com.marketlens.phone

/**
 * The MarketLens PC's address on the home network, as the owner types it ("192.168.0.10" or "192.168.0.10:8766").
 * Only private IPv4 addresses (RFC 1918) are accepted: the app never talks to anything on the internet.
 */
data class PcAddress(val host: String, val port: Int) {
    val base: String get() = "http://$host:$port"

    /** A URL of this PC (same host and port) — the only pages the app opens inside itself. */
    fun owns(url: String): Boolean {
        val u = runCatching { java.net.URI(url) }.getOrNull() ?: return false
        val p = if (u.port == -1) 80 else u.port
        return u.scheme == "http" && u.host == host && p == port
    }

    companion object {
        const val DEFAULT_PORT = 8766

        /** The address, or null with nothing guessed when it is not a home-network IPv4 address. */
        fun parse(text: String): PcAddress? {
            var t = text.trim().removePrefix("http://").trimEnd('/')
            if (t.isEmpty() || t.contains('/') || t.contains('@')) return null
            var port = DEFAULT_PORT
            val colon = t.lastIndexOf(':')
            if (colon >= 0) {
                port = t.substring(colon + 1).toIntOrNull() ?: return null
                if (port !in 1..65535) return null
                t = t.substring(0, colon)
            }
            val parts = t.split('.')
            if (parts.size != 4) return null
            val n = parts.map { it.toIntOrNull()?.takeIf { v -> v in 0..255 && it == v.toString() } ?: return null }
            val private = n[0] == 10 || (n[0] == 172 && n[1] in 16..31) || (n[0] == 192 && n[1] == 168)
            return if (private) PcAddress(t, port) else null
        }
    }
}
