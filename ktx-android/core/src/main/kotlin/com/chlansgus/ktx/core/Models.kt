package com.chlansgus.ktx.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

internal fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.content

private fun hhmm(time: String?) = if (time != null && time.length >= 4) "${time.substring(0, 2)}:${time.substring(2, 4)}" else "--:--"

private fun monthDay(date: String?) =
    if (date != null && date.length == 8) "${date.substring(4, 6).toInt()}월 ${date.substring(6).toInt()}일" else ""

data class Train(
    val trainType: String,
    val trainTypeName: String,
    val trainGroup: String,
    val trainNo: String,
    val depName: String,
    val depCode: String,
    val depDate: String,
    val depTime: String,
    val arrName: String,
    val arrCode: String,
    val arrDate: String,
    val arrTime: String,
    val runDate: String,
    /** 00: 없음, 11: 예약 가능, 13: 매진 */
    val specialSeat: String,
    val generalSeat: String,
) {
    fun hasGeneralSeat() = generalSeat == "11"
    fun hasSpecialSeat() = specialSeat == "11"
    fun hasSeat() = hasGeneralSeat() || hasSpecialSeat()
    fun depHhmm() = hhmm(depTime)
    fun arrHhmm() = hhmm(arrTime)

    companion object {
        fun parse(d: JsonObject) = Train(
            trainType = d.str("h_trn_clsf_cd").orEmpty(),
            trainTypeName = d.str("h_trn_clsf_nm").orEmpty(),
            trainGroup = d.str("h_trn_gp_cd").orEmpty(),
            trainNo = d.str("h_trn_no").orEmpty(),
            depName = d.str("h_dpt_rs_stn_nm").orEmpty(),
            depCode = d.str("h_dpt_rs_stn_cd").orEmpty(),
            depDate = d.str("h_dpt_dt").orEmpty(),
            depTime = d.str("h_dpt_tm").orEmpty(),
            arrName = d.str("h_arv_rs_stn_nm").orEmpty(),
            arrCode = d.str("h_arv_rs_stn_cd").orEmpty(),
            arrDate = d.str("h_arv_dt").orEmpty(),
            arrTime = d.str("h_arv_tm").orEmpty(),
            runDate = d.str("h_run_dt").orEmpty(),
            specialSeat = d.str("h_spe_rsv_cd").orEmpty(),
            generalSeat = d.str("h_gen_rsv_cd").orEmpty(),
        )
    }
}

/** 예약 결과. 예약번호는 항상 있고, 예약내역 조회에 실패하면 상세 정보가 없습니다. */
data class Reservation(
    val rsvId: String,
    val trainTypeName: String? = null,
    val trainNo: String? = null,
    val depName: String? = null,
    val arrName: String? = null,
    val runDate: String? = null,
    val depTime: String? = null,
    val arrTime: String? = null,
    val seatCount: Int? = null,
    val price: Int? = null,
    val buyLimitDate: String? = null,
    val buyLimitTime: String? = null,
) {
    val confirmed get() = depTime != null

    fun describe(): String {
        if (!confirmed) return "예약번호 $rsvId · 상세 정보를 불러오지 못했습니다. 코레일 예약내역에서 확인하세요."
        val price = price?.let { "%,d원".format(it) } ?: "-"
        return "[$trainTypeName $trainNo] ${monthDay(runDate)} $depName~$arrName(${hhmm(depTime)}~${hhmm(arrTime)})\n" +
            "$price (${seatCount ?: "-"}석) · 결제기한 ${monthDay(buyLimitDate)} ${hhmm(buyLimitTime)}"
    }

    companion object {
        fun parse(d: JsonObject) = Reservation(
            rsvId = d.str("h_pnr_no").orEmpty(),
            trainTypeName = d.str("h_trn_clsf_nm"),
            trainNo = d.str("h_trn_no"),
            depName = d.str("h_dpt_rs_stn_nm"),
            arrName = d.str("h_arv_rs_stn_nm"),
            runDate = d.str("h_run_dt"),
            depTime = d.str("h_dpt_tm"),
            arrTime = d.str("h_arv_tm"),
            seatCount = d.str("h_tot_seat_cnt")?.trim()?.toIntOrNull(),
            price = d.str("h_rsv_amt")?.trim()?.toIntOrNull(),
            buyLimitDate = d.str("h_ntisu_lmt_dt"),
            buyLimitTime = d.str("h_ntisu_lmt_tm"),
        )
    }
}

open class KorailException(val msg: String, val code: String? = null) :
    RuntimeException(if (code.isNullOrEmpty()) msg else "$msg ($code)")

class NoResultsException(code: String? = null) : KorailException("No Results", code) {
    companion object { val CODES = setOf("P100", "WRG000000", "WRD000061", "WRT300005") }
}

class NeedToLoginException(code: String? = null) : KorailException("로그인이 필요합니다. 다시 로그인하세요.", code) {
    companion object { val CODES = setOf("P058") }
}

class SoldOutException(code: String? = null) : KorailException("Sold out", code) {
    companion object { val CODES = setOf("ERR211161") }
}

/** HTTP 오류, 해석할 수 없는 응답 등. 메시지는 그대로 사용자에게 보여줍니다. */
class ServerException(message: String) : RuntimeException(message)
