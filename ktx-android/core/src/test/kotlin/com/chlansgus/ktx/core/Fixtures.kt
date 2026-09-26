package com.chlansgus.ktx.core

import okhttp3.mockwebserver.MockResponse

fun ok(body: String) = MockResponse().setHeader("Content-Type", "application/json").setBody(body)

fun trainJson(no: String, dep: String, gen: String = "11", spe: String = "13", date: String = "20300101") = """
{"h_trn_clsf_cd":"00","h_trn_clsf_nm":"KTX","h_trn_gp_cd":"100","h_trn_no":"$no",
 "h_dpt_rs_stn_nm":"서울","h_dpt_rs_stn_cd":"0001","h_dpt_dt":"$date","h_dpt_tm":"$dep",
 "h_arv_rs_stn_nm":"부산","h_arv_rs_stn_cd":"0020","h_arv_dt":"$date","h_arv_tm":"235900",
 "h_run_dt":"$date","h_spe_rsv_cd":"$spe","h_gen_rsv_cd":"$gen","h_rsv_psb_flg":"Y","h_wait_rsv_flg":"-1"}
"""

fun searchJson(vararg trains: String) =
    """{"strResult":"SUCC","trn_infos":{"trn_info":[${trains.joinToString(",")}]}}"""

const val NO_RESULTS = """{"strResult":"FAIL","h_msg_cd":"P100","h_msg_txt":"조회 결과가 없습니다."}"""
const val SOLD_OUT = """{"strResult":"FAIL","h_msg_cd":"ERR211161","h_msg_txt":"잔여석없음"}"""
const val RESERVE_OK = """{"strResult":"SUCC","h_pnr_no":"PNR123"}"""
const val RESERVATIONS = """{"strResult":"SUCC","jrny_infos":{"jrny_info":[{"train_infos":{"train_info":[
 {"h_pnr_no":"PNR123","h_trn_clsf_nm":"KTX","h_trn_no":"101","h_dpt_rs_stn_nm":"서울","h_arv_rs_stn_nm":"부산",
  "h_run_dt":"20300101","h_dpt_tm":"060000","h_arv_tm":"083000","h_tot_seat_cnt":"001","h_rsv_amt":"00059800",
  "h_ntisu_lmt_dt":"20300101","h_ntisu_lmt_tm":"052000"}]}}]}}"""
