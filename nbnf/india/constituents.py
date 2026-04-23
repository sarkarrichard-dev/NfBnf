from __future__ import annotations

"""
Static index constituents for the desk picker (Yahoo NSE .NS symbols).

``rank`` within each index is an approximate free-float / index-weight tier
(1 = largest typical influence in that index). Rebalance with NSE factsheets periodically.
"""

from typing import Any

# (rank, yahoo_symbol, display_name)
_NIFTY50: list[tuple[int, str, str]] = [
    (1, "HDFCBANK.NS", "HDFC Bank"),
    (2, "RELIANCE.NS", "Reliance Industries"),
    (3, "ICICIBANK.NS", "ICICI Bank"),
    (4, "TCS.NS", "Tata Consultancy Services"),
    (5, "INFY.NS", "Infosys"),
    (6, "BHARTIARTL.NS", "Bharti Airtel"),
    (7, "SBIN.NS", "State Bank of India"),
    (8, "ITC.NS", "ITC"),
    (9, "HINDUNILVR.NS", "Hindustan Unilever"),
    (10, "KOTAKBANK.NS", "Kotak Mahindra Bank"),
    (11, "LT.NS", "Larsen & Toubro"),
    (12, "MARUTI.NS", "Maruti Suzuki"),
    (13, "AXISBANK.NS", "Axis Bank"),
    (14, "BAJFINANCE.NS", "Bajaj Finance"),
    (15, "ASIANPAINT.NS", "Asian Paints"),
    (16, "M&M.NS", "Mahindra & Mahindra"),
    (17, "SUNPHARMA.NS", "Sun Pharmaceutical"),
    (18, "ULTRACEMCO.NS", "UltraTech Cement"),
    (19, "NESTLEIND.NS", "Nestlé India"),
    (20, "TITAN.NS", "Titan"),
    (21, "HCLTECH.NS", "HCL Technologies"),
    (22, "BAJAJFINSV.NS", "Bajaj Finserv"),
    (23, "WIPRO.NS", "Wipro"),
    (24, "ONGC.NS", "ONGC"),
    (25, "NTPC.NS", "NTPC"),
    (26, "POWERGRID.NS", "Power Grid"),
    (27, "ADANIENT.NS", "Adani Enterprises"),
    (28, "JSWSTEEL.NS", "JSW Steel"),
    (29, "ADANIPORTS.NS", "Adani Ports"),
    (30, "TATAMOTORS.NS", "Tata Motors"),
    (31, "DIVISLAB.NS", "Divi's Laboratories"),
    (32, "TECHM.NS", "Tech Mahindra"),
    (33, "COALINDIA.NS", "Coal India"),
    (34, "DRREDDY.NS", "Dr Reddy's"),
    (35, "CIPLA.NS", "Cipla"),
    (36, "EICHERMOT.NS", "Eicher Motors"),
    (37, "GRASIM.NS", "Grasim"),
    (38, "APOLLOHOSP.NS", "Apollo Hospitals"),
    (39, "BPCL.NS", "BPCL"),
    (40, "HEROMOTOCO.NS", "Hero MotoCorp"),
    (41, "INDUSINDBK.NS", "IndusInd Bank"),
    (42, "SBILIFE.NS", "SBI Life"),
    (43, "HDFCLIFE.NS", "HDFC Life"),
    (44, "TATASTEEL.NS", "Tata Steel"),
    (45, "HINDALCO.NS", "Hindalco"),
    (46, "BAJAJ-AUTO.NS", "Bajaj Auto"),
    (47, "SHRIRAMFIN.NS", "Shriram Finance"),
    (48, "JIOFIN.NS", "Jio Financial"),
    (49, "TRENT.NS", "Trent"),
    (50, "BEL.NS", "Bharat Electronics"),
]

_NIFTY_BANK: list[tuple[int, str, str]] = [
    (1, "HDFCBANK.NS", "HDFC Bank"),
    (2, "ICICIBANK.NS", "ICICI Bank"),
    (3, "SBIN.NS", "State Bank of India"),
    (4, "KOTAKBANK.NS", "Kotak Mahindra Bank"),
    (5, "AXISBANK.NS", "Axis Bank"),
    (6, "INDUSINDBK.NS", "IndusInd Bank"),
    (7, "BANKBARODA.NS", "Bank of Baroda"),
    (8, "PNB.NS", "Punjab National Bank"),
    (9, "FEDERALBNK.NS", "Federal Bank"),
    (10, "IDFCFIRSTB.NS", "IDFC First Bank"),
    (11, "BANDHANBNK.NS", "Bandhan Bank"),
    (12, "AUBANK.NS", "AU Small Finance Bank"),
]


def _rows(rows: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    return [{"rank": r, "symbol": s, "name": n} for r, s, n in sorted(rows, key=lambda x: x[0])]


def get_indices_catalog() -> dict[str, Any]:
    return {
        "version": "2026-04-desk",
        "disclaimer": (
            "Static snapshot for UI only. Index membership and weights change — "
            "confirm with NSE / Nifty Indices before trading."
        ),
        "categories": [
            {
                "id": "broad",
                "label": "Broad market",
                "indices": [
                    {
                        "id": "nifty50",
                        "label": "Nifty 50",
                        "rank_note": "Approximate index-weight tier (1 = largest typical weight).",
                        "stocks": _rows(_NIFTY50),
                    },
                ],
            },
            {
                "id": "sectoral",
                "label": "Sectoral",
                "indices": [
                    {
                        "id": "niftybank",
                        "label": "Nifty Bank",
                        "rank_note": "Approximate tier within the banking index (1 = largest typical weight).",
                        "stocks": _rows(_NIFTY_BANK),
                    },
                ],
            },
        ],
    }
