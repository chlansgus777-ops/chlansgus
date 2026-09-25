"""SEC SIC code → sector / industry labels used by the sector models (free, no paid classification feed).

Industry labels are chosen so the keyword matching in config/sector_models.toml selects the right model.
"""

from __future__ import annotations

# exact codes first
SIC_EXACT: dict[int, tuple[str, str]] = {
    3674: ("Technology", "Semiconductors"),
    3559: ("Technology", "Semiconductor Equipment & Materials"),
    3825: ("Technology", "Semiconductor Equipment & Materials"),
    7372: ("Technology", "Software—Application"),
    7371: ("Technology", "Software—Infrastructure"),
    7373: ("Technology", "Software—Infrastructure"),
    7374: ("Technology", "Software—Infrastructure"),
    7370: ("Communication Services", "Internet Content & Information"),
    7389: ("Industrials", "Business Services"),
    5961: ("Consumer Cyclical", "Internet Retail"),
    3571: ("Technology", "Computer Hardware"),
    3572: ("Technology", "Computer Hardware"),
    3576: ("Technology", "Communication Equipment"),
    3577: ("Technology", "Computer Hardware"),
    3661: ("Technology", "Communication Equipment"),
    3663: ("Technology", "Communication Equipment"),
    3669: ("Technology", "Communication Equipment"),
    3672: ("Technology", "Electronic Components"),
    3678: ("Technology", "Electronic Components"),
    3679: ("Technology", "Electronic Components"),
    4813: ("Communication Services", "Telecom Services"),
    4841: ("Communication Services", "Entertainment"),
    6021: ("Financial Services", "Banks—Diversified"),
    6022: ("Financial Services", "Banks—Regional"),
    6029: ("Financial Services", "Banks—Regional"),
    6035: ("Financial Services", "Banks—Regional"),
    6036: ("Financial Services", "Banks—Regional"),
    6199: ("Financial Services", "Credit Services"),
    6141: ("Financial Services", "Credit Services"),
    6211: ("Financial Services", "Capital Markets"),
    6282: ("Financial Services", "Capital Markets"),
    6311: ("Financial Services", "Insurance—Life"),
    6331: ("Financial Services", "Insurance—Property & Casualty"),
    6411: ("Financial Services", "Insurance Brokers"),
    6798: ("Real Estate", "REIT—Diversified"),
    2834: ("Healthcare", "Drug Manufacturers—General"),
    2835: ("Healthcare", "Diagnostics & Research"),
    2836: ("Healthcare", "Biotechnology"),
    8731: ("Healthcare", "Biotechnology"),
    3841: ("Healthcare", "Medical Devices"),
    3842: ("Healthcare", "Medical Devices"),
    3845: ("Healthcare", "Medical Devices"),
    8071: ("Healthcare", "Diagnostics & Research"),
    6324: ("Healthcare", "Healthcare Plans"),
    1311: ("Energy", "Oil & Gas E&P"),
    2911: ("Energy", "Oil & Gas Integrated"),
    1381: ("Energy", "Oil & Gas Drilling"),
    1389: ("Energy", "Oil & Gas Equipment & Services"),
    4922: ("Energy", "Oil & Gas Midstream"),
    4911: ("Utilities", "Utilities—Regulated Electric"),
    4931: ("Utilities", "Utilities—Diversified"),
    4932: ("Utilities", "Utilities—Regulated Gas"),
    4941: ("Utilities", "Utilities—Regulated Water"),
    4991: ("Utilities", "Utilities—Independent Power Producers"),
    3721: ("Industrials", "Aerospace & Defense"),
    3724: ("Industrials", "Aerospace & Defense"),
    3728: ("Industrials", "Aerospace & Defense"),
    3812: ("Industrials", "Aerospace & Defense"),
    4512: ("Industrials", "Airlines"),
    3711: ("Consumer Cyclical", "Auto Manufacturers"),
    5812: ("Consumer Cyclical", "Restaurants"),
    2080: ("Consumer Defensive", "Beverages"),
    2086: ("Consumer Defensive", "Beverages"),
    2844: ("Consumer Defensive", "Household & Personal Products"),
    2840: ("Consumer Defensive", "Household & Personal Products"),
    5331: ("Consumer Defensive", "Discount Stores"),
    5411: ("Consumer Defensive", "Grocery Stores"),
    2000: ("Consumer Defensive", "Packaged Foods"),
}

# SIC 2-digit major groups → (sector, industry) fallback
SIC_MAJOR: dict[int, tuple[str, str]] = {
    10: ("Basic Materials", "Metals & Mining"), 12: ("Energy", "Coal"), 13: ("Energy", "Oil & Gas E&P"),
    14: ("Basic Materials", "Mining"), 15: ("Industrials", "Engineering & Construction"), 16: ("Industrials", "Engineering & Construction"),
    17: ("Industrials", "Engineering & Construction"), 20: ("Consumer Defensive", "Packaged Foods"), 21: ("Consumer Defensive", "Tobacco"),
    22: ("Consumer Cyclical", "Textiles"), 23: ("Consumer Cyclical", "Apparel"), 24: ("Basic Materials", "Lumber"),
    25: ("Consumer Cyclical", "Furnishings"), 26: ("Basic Materials", "Paper"), 27: ("Communication Services", "Publishing"),
    28: ("Basic Materials", "Chemicals"), 29: ("Energy", "Oil & Gas Refining"), 30: ("Basic Materials", "Rubber & Plastics"),
    31: ("Consumer Cyclical", "Footwear"), 32: ("Basic Materials", "Building Materials"), 33: ("Basic Materials", "Steel"),
    34: ("Industrials", "Metal Fabrication"), 35: ("Industrials", "Machinery"), 36: ("Industrials", "Electrical Equipment & Parts"),
    37: ("Industrials", "Aerospace & Defense"), 38: ("Healthcare", "Medical Instruments"), 39: ("Consumer Cyclical", "Leisure"),
    40: ("Industrials", "Railroads"), 42: ("Industrials", "Trucking"), 44: ("Industrials", "Marine Shipping"),
    45: ("Industrials", "Airlines"), 46: ("Energy", "Oil & Gas Midstream"), 47: ("Industrials", "Logistics"),
    48: ("Communication Services", "Telecom Services"), 49: ("Utilities", "Utilities—Diversified"),
    50: ("Industrials", "Industrial Distribution"), 51: ("Consumer Defensive", "Food Distribution"),
    52: ("Consumer Cyclical", "Home Improvement Retail"), 53: ("Consumer Defensive", "Discount Stores"),
    54: ("Consumer Defensive", "Grocery Stores"), 55: ("Consumer Cyclical", "Auto Dealerships"), 56: ("Consumer Cyclical", "Apparel Retail"),
    57: ("Consumer Cyclical", "Specialty Retail"), 58: ("Consumer Cyclical", "Restaurants"), 59: ("Consumer Cyclical", "Specialty Retail"),
    60: ("Financial Services", "Banks—Regional"), 61: ("Financial Services", "Credit Services"), 62: ("Financial Services", "Capital Markets"),
    63: ("Financial Services", "Insurance—Diversified"), 64: ("Financial Services", "Insurance Brokers"), 65: ("Real Estate", "Real Estate Services"),
    67: ("Financial Services", "Asset Management"), 70: ("Consumer Cyclical", "Lodging"), 72: ("Consumer Cyclical", "Personal Services"),
    73: ("Technology", "Software—Infrastructure"), 75: ("Consumer Cyclical", "Auto Services"), 78: ("Communication Services", "Entertainment"),
    79: ("Consumer Cyclical", "Leisure"), 80: ("Healthcare", "Medical Care Facilities"), 82: ("Consumer Defensive", "Education"),
    87: ("Industrials", "Consulting Services"),
}


def classify_sic(sic: int | None) -> tuple[str, str]:
    if sic is None:
        return "Unknown", "Unknown"
    if sic in SIC_EXACT:
        return SIC_EXACT[sic]
    return SIC_MAJOR.get(sic // 100, ("Unknown", "Unknown"))
