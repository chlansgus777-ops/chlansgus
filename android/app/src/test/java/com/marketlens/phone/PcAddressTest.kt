package com.marketlens.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PcAddressTest {
    @Test
    fun homeNetworkAddressesWithOrWithoutPort() {
        assertEquals(PcAddress("192.168.0.10", 8766), PcAddress.parse("192.168.0.10"))
        assertEquals(PcAddress("10.0.0.5", 9000), PcAddress.parse(" http://10.0.0.5:9000/ "))
        assertEquals(PcAddress("172.20.1.2", 8766), PcAddress.parse("172.20.1.2"))
    }

    @Test
    fun nothingOutsideTheHomeNetworkAndNothingGuessed() {
        for (bad in listOf("", "8.8.8.8", "172.32.0.1", "192.169.0.1", "127.0.0.1", "example.com", "192.168.0.10:0",
                "192.168.0.10:99999", "192.168.0.10/x", "user@192.168.0.10", "192.168.00.10", "192.168.0", "https://192.168.0.10")) {
            assertNull(bad, PcAddress.parse(bad))
        }
    }

    @Test
    fun onlyThePcsOwnPagesStayInTheApp() {
        val a = PcAddress("192.168.0.10", 8766)
        assertTrue(a.owns("http://192.168.0.10:8766/stocks/NVDA"))
        assertFalse(a.owns("http://192.168.0.10:8767/"))
        assertFalse(a.owns("https://192.168.0.10:8766/"))
        assertFalse(a.owns("http://evil.example.com/"))
    }
}
