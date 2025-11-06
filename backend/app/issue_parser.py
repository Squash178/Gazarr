"""Magazine issue parsing using LazyLibrarian logic.

This module embeds LazyLibrarian's magazine date parsing routine so Gazarr can
normalise issue strings identically. The implementation is licensed under the
GNU General Public License v3, consistent with LazyLibrarian's licensing.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Static month data (adapted from LazyLibrarian defaults: English, Spanish, German)
# ---------------------------------------------------------------------------

_BASE_MONTH_TABLE = [
    ["en_GB.UTF-8", "en_GB.UTF-8", "es_ES.UTF8", "es_ES.UTF8", "de_DE.UTF8", "de_DE.UTF8"],
    ["January", "Jan", "enero", "ene", "Januar", "Jan"],
    ["February", "Feb", "febrero", "feb", "Februar", "Feb"],
    ["March", "Mar", "marzo", "mar", "März", "Mär"],
    ["April", "Apr", "abril", "abr", "April", "Apr"],
    ["May", "May", "mayo", "may", "Mai", "Mai"],
    ["June", "Jun", "junio", "jun", "Juni", "Jun"],
    ["July", "Jul", "julio", "jul", "Juli", "Jul"],
    ["August", "Aug", "agosto", "ago", "August", "Aug"],
    ["September", "Sep", "septiembre", "sep", "September", "Sep"],
    ["October", "Oct", "octubre", "oct", "Oktober", "Okt"],
    ["November", "Nov", "noviembre", "nov", "November", "Nov"],
    ["December", "Dec", "diciembre", "dic", "Dezember", "Dez"],
]

_SEASONS = {
    "spring": 3,
    "summer": 6,
    "autumn": 9,
    "fall": 9,
    "winter": 12,
}

ISSUE_NOUNS = ["issue", "iss", "no", "nr", "number", "#"]
VOLUME_NOUNS = ["vol", "volume", "vol."]


# ---------------------------------------------------------------------------
# Utility helpers (ported from LazyLibrarian)
# ---------------------------------------------------------------------------


def unaccented(text: str) -> str:
    return ''.join(ch for ch in unicodedata.normalize('NFD', text or '') if unicodedata.category(ch) != 'Mn')


def _build_clean_table(table: List[List[str]]) -> List[List[str]]:
    clean: List[List[str]] = []
    for row in table:
        clean_row = []
        for item in row:
            clean_row.append(unaccented(item).lower().strip('.'))
        clean.append(clean_row)
    return clean


MONTH_TABLE = [_BASE_MONTH_TABLE, _build_clean_table(_BASE_MONTH_TABLE)]


def replace_all(text: str, replacements: Dict[str, str]) -> str:
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def check_int(val: Optional[str], default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def check_year(val: str) -> int:
    if val and val.isdigit():
        year = int(val)
        if 1900 <= year <= datetime.datetime.utcnow().year + 1:
            return year
    return 0


def get_list(value) -> List[str]:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if not value:
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]


def month2num(month: str) -> int:
    clean = unaccented(month).lower()
    for idx in range(1, len(MONTH_TABLE[0])):
        row = MONTH_TABLE[0][idx]
        if any(month.lower() == name.lower() for name in row):
            return idx
        if clean in MONTH_TABLE[1][idx]:
            return idx
    if clean in _SEASONS:
        return _SEASONS[clean]
    return 0


def two_months(word: str) -> (int, int):
    a = 0
    b = 0
    cleanword = unaccented(word).lower()
    for f in range(1, 13):
        for month in MONTH_TABLE[0][f]:
            if word.startswith(month):
                a = f
                break
        if not a:
            for month in MONTH_TABLE[1][f]:
                if cleanword.startswith(month):
                    a = f
        if a:
            break
    if a:
        for f in range(1, 13):
            for month in MONTH_TABLE[0][f]:
                if word.endswith(month):
                    b = f
                    break
            if not b:
                for month in MONTH_TABLE[1][f]:
                    if cleanword.endswith(month):
                        b = f
            if b:
                break
    if a == b:
        return 0, 0
    return a, b


# ---------------------------------------------------------------------------
# Primary data structures
# ---------------------------------------------------------------------------


@dataclass
class IssueMetadata:
    issue_code: str
    label: str
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    issue_number: Optional[int] = None
    volume: Optional[int] = None


# ---------------------------------------------------------------------------
# Full LazyLibrarian parsing logic
# ---------------------------------------------------------------------------


def parse_issue(title: str, magazine_title: Optional[str] = None, language: str = "en") -> Optional[IssueMetadata]:
    stripped = _strip_magazine_title(title, magazine_title)
    dateparts = get_dateparts(stripped, datetype='', language=language)
    if not dateparts:
        return None
    dbdate = dateparts.get('dbdate')
    if not dbdate:
        return None
    issue_number = dateparts.get('issue')
    return IssueMetadata(
        issue_code=dbdate,
        label=_format_label(dateparts, language),
        year=dateparts.get('year'),
        month=dateparts.get('month'),
        day=dateparts.get('day'),
        issue_number=int(issue_number) if issue_number else None,
        volume=dateparts.get('volume'),
    )


def _strip_magazine_title(title: str, magazine_title: Optional[str]) -> str:
    if not magazine_title:
        return title

    target = ''.join(ch.lower() for ch in magazine_title if ch.isalnum())
    if not target:
        return title

    count = 0
    ti = 0
    while ti < len(title) and count < len(target):
        ch = title[ti]
        if ch.isalnum():
            if ch.lower() != target[count]:
                return title
            count += 1
        ti += 1

    if count < len(target):
        return title

    while ti < len(title) and title[ti] in " .-_:/\\|–—":
        ti += 1
    return title[ti:]


def get_dateparts(title_or_issue: str, datetype: str = '', language: str = 'en') -> Optional[Dict[str, Optional[int]]]:
    dic = {'.': ' ', '-': ' ', '/': ' ', '+': ' ', '_': ' ', '(': '', ')': '', '[': ' ', ']': ' ', '#': '# '}
    words = replace_all(title_or_issue, dic).split()
    issuenouns = get_list(ISSUE_NOUNS)
    volumenouns = get_list(VOLUME_NOUNS)
    nouns = issuenouns + volumenouns

    year = 0
    months: List[int] = []
    day = 0
    issue = 0
    volume = 0
    style = 0
    month = 0
    mname = ''
    inoun = ''
    vnoun = ''

    pos = 0
    while pos < len(words):
        if not year:
            year = check_year(words[pos])
        month_val = month2num(words[pos])
        if month_val:
            mname = words[pos]
            months.append(month_val)
        else:
            month_a, month_b = two_months(words[pos])
            if month_a:
                months.append(month_a)
                months.append(month_b)
        lower_word = words[pos].lower().strip('.')
        if lower_word in issuenouns:
            if pos + 1 < len(words):
                inoun = words[pos]
                pos += 1
                issue = check_int(words[pos], 0)
        elif lower_word in volumenouns:
            if pos + 1 < len(words):
                vnoun = words[pos]
                pos += 1
                volume = check_int(words[pos], 0)
        pos += 1

    months = list(dict.fromkeys(months))
    if len(months) > 1:
        style = 1
    if months:
        month = months[0]

    if volume and issue:
        style = 8 if year else 9

    pos = 0
    while pos < len(words):
        data = words[pos]
        if data.isdigit():
            if len(data) == 4 and check_year(data):
                year = int(data)
            elif len(data) == 6:
                if check_year(data[:4]):
                    year = int(data[:4])
                    issue = int(data[4:])
                    style = 13
                elif check_year(data[2:]):
                    year = int(data[2:])
                    issue = int(data[:2])
                    style = 13
            elif len(data) == 8:
                if check_year(data[:4]):
                    year = int(data[:4])
                    issue = int(data[4:])
                    style = 16
                else:
                    volume = int(data[:4])
                    issue = int(data[4:])
                    style = 17
            elif len(data) == 12:
                year = int(data[:4])
                volume = int(data[4:8])
                issue = int(data[8:])
                style = 18
            elif len(data) > 2:
                issue = int(data)
        pos += 1

    dateparts: Dict[str, Optional[int]] = {
        "year": year,
        "months": months,
        "day": day,
        "issue": issue,
        "volume": volume,
        "month": month,
        "mname": mname,
        "inoun": inoun,
        "vnoun": vnoun,
        "style": style,
    }

    if not dateparts['style']:
        pos = 0
        while pos < len(words):
            year = check_year(words[pos])
            if year and pos:
                month = month2num(words[pos - 1])
                if month:
                    if pos > 1:
                        day = check_int(re.sub(r"\D", "", words[pos - 2]), 0)
                        if pos > 2 and words[pos - 3].lower().strip('.') in issuenouns:
                            dateparts['issue'] = day
                            dateparts['inoun'] = words[pos - 3]
                            dateparts['style'] = 10
                            break
                        elif pos > 2 and words[pos - 3].lower().strip('.') in volumenouns:
                            dateparts['volume'] = day
                            dateparts['vnoun'] = words[pos - 3]
                            dateparts['style'] = 10
                            break
                        elif day > 31:
                            if 'I' in datetype:
                                dateparts['issue'] = day
                                dateparts['style'] = 10
                                break
                            elif 'V' in datetype:
                                dateparts['volume'] = day
                                dateparts['style'] = 10
                                break
                            else:
                                dateparts['issue'] = day
                                dateparts['style'] = 2
                                break
                        elif day:
                            dateparts['style'] = 3
                            dateparts['day'] = day
                            break
                        else:
                            dateparts['style'] = 4
                            dateparts['day'] = 1
                            break
                    else:
                        dateparts['style'] = 4
                        dateparts['day'] = 1
                        break
            pos += 1

        if not dateparts['style']:
            pos = 0
            while pos < len(words):
                year = check_year(words[pos])
                if year and (pos > 1):
                    month = month2num(words[pos - 2])
                    if month:
                        day = check_int(re.sub(r"\D", "", words[pos - 1]), 0)
                        try:
                            _ = datetime.date(year, month, day)
                            dateparts['year'] = year
                            dateparts['month'] = month
                            if not dateparts['months']:
                                dateparts['months'].append(month)
                            dateparts['day'] = day
                            dateparts['style'] = 5
                            break
                        except (ValueError, OverflowError):
                            pass
                pos += 1

        if not dateparts['style']:
            pos = 0
            while pos < len(words):
                year = check_year(words[pos])
                if year and pos + 1 < len(words):
                    month = month2num(words[pos + 1])
                    if not month:
                        month = check_int(words[pos + 1], 0)
                        if month > 12:
                            month = 0
                    if month:
                        if pos + 2 < len(words):
                            day = check_int(re.sub(r"\D", "", words[pos + 2]), 0)
                            if day:
                                style = 6
                            else:
                                day = 1
                                style = 7
                        else:
                            day = 1
                            style = 7
                        try:
                            _ = datetime.date(year, month, day)
                            dateparts['year'] = year
                            dateparts['month'] = month
                            if not dateparts['months']:
                                dateparts['months'].append(month)
                            dateparts['day'] = day
                            dateparts['style'] = style
                        except (ValueError, OverflowError):
                            dateparts['style'] = 0
                pos += 1

        if not dateparts['style']:
            pos = 0
            while pos < len(words):
                splitted = re.split(r'(\d+)', words[pos].lower())
                if splitted[0].strip('.') in nouns:
                    if len(splitted) > 1:
                        issue = check_int(splitted[1], 0)
                        if issue:
                            dateparts['issue'] = issue
                            dateparts['style'] = 10 if dateparts['year'] else 11
                            break
                    if pos + 1 < len(words):
                        issue = check_int(words[pos + 1], 0)
                        if issue:
                            dateparts['issue'] = issue
                            dateparts['style'] = 10 if dateparts['year'] else 11
                            break
                        issue_token = words[pos + 1]
                        if issue_token.count('.') == 1 and issue_token.replace('.', '').isdigit():
                            year_part, issue_part = issue_token.split('.')
                            if len(year_part) == 2:
                                year_part = f"20{year_part}"
                            if len(issue_part) == 1:
                                issue_part = f"0{issue_part}"
                            if len(year_part) == 4 and len(issue_part) == 2:
                                dateparts['year'] = int(year_part)
                                dateparts['issue'] = int(issue_part)
                                dateparts['style'] = 10
                                break
                pos += 1

        if not dateparts['style'] and dateparts['year']:
            pos = 1
            while pos < len(words):
                if check_year(words[pos]):
                    if words[pos - 1].isdigit():
                        if pos > 1 and words[pos - 2].isdigit():
                            m = int(words[pos - 1])
                            d = int(words[pos - 2])
                            if m == 1 and d < 13:
                                m = d
                                d = 1
                            if m < 13:
                                dateparts['months'] = [m]
                                dateparts['day'] = d
                                dateparts['style'] = 3
                            elif d < 13:
                                dateparts['months'] = [d]
                                dateparts['day'] = m
                                dateparts['style'] = 3
                        if not dateparts['style']:
                            dateparts['issue'] = int(words[pos - 1])
                            dateparts['style'] = 12
                        break
                    elif pos + 1 < len(words) and words[pos + 1].isdigit():
                        dateparts['issue'] = int(words[pos + 1])
                        dateparts['style'] = 12
                        break
                pos += 1

    if dateparts['months']:
        dateparts['month'] = dateparts['months'][0]
    else:
        dateparts['month'] = 0

    if dateparts['year'] and not dateparts['style']:
        dateparts['style'] = 15

    if dateparts['issue'] and not dateparts['style']:
        dateparts['style'] = 14

    if not dateparts['style'] and datetype == 'I':
        numbers = [word for word in words if word.isdigit()]
        if len(numbers) == 1:
            dateparts['issue'] = int(numbers[0])
            dateparts['style'] = 14

    datetype_ok = True
    if datetype and dateparts['style']:
        if 'M' in datetype and (dateparts['style'] not in [1, 2, 3, 4, 5, 6, 7, 12] or not dateparts['month']):
            datetype_ok = False
        if 'D' in datetype and (dateparts['style'] not in [3, 5, 6] or not dateparts['day']):
            datetype_ok = False
        if 'MM' in datetype and (dateparts['style'] not in [1] or len(dateparts['months']) < 2):
            datetype_ok = False
        if 'V' in datetype and (dateparts['style'] not in [2, 8, 9, 10, 11, 12, 13, 14, 17, 18]
                                or not dateparts['volume']):
            datetype_ok = False
        if 'I' in datetype and (dateparts['style'] not in [2, 10, 11, 12, 13, 14, 16, 17, 18]
                                or not dateparts['issue']):
            datetype_ok = False
        if 'Y' in datetype and (dateparts['style'] not in [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 13, 15, 16, 18]
                                or not dateparts['year']):
            datetype_ok = False
    if not datetype_ok:
        dateparts['style'] = 0
    else:
        if dateparts['issue'] and ('I' in datetype or dateparts['inoun']):
            issuenum = str(dateparts['issue']).zfill(4)
            if dateparts['year']:
                issuenum = f"{dateparts['year']}{issuenum}"
        else:
            if not dateparts['day']:
                dateparts['day'] = 1
            if dateparts['style'] == 14:
                issuenum = f"{dateparts['issue']:04d}"
            elif dateparts['style'] == 15:
                issuenum = f"{dateparts['year']}"
            elif dateparts['style'] == 16:
                issuenum = f"{dateparts['year']}{dateparts['issue']:04d}"
            elif dateparts['style'] == 17:
                issuenum = f"{dateparts['volume']:04d}{dateparts['issue']:04d}"
            elif dateparts['style'] == 18:
                issuenum = f"{dateparts['year']}{dateparts['volume']:04d}{dateparts['issue']:04d}"
            else:
                issuenum = f"{dateparts['year']}-{dateparts['month']:02d}-{dateparts['day']:02d}"
        dateparts['dbdate'] = issuenum

    return dateparts if dateparts['style'] else None


def _format_label(dateparts: Dict[str, Optional[int]], language: str) -> str:
    year = dateparts.get('year')
    month = dateparts.get('month')
    months = dateparts.get('months', [])
    issue = dateparts.get('issue')
    volume = dateparts.get('volume')

    if month and year:
        if isinstance(months, list) and len(months) > 1:
            names = [_month_name(m, language) for m in months]
            return f"{'/'.join(names)} {year}"
        return f"{_month_name(month, language)} {year}"

    components: List[str] = []
    if volume:
        components.append(f"Vol {volume}")
    if issue:
        components.append(f"Issue {issue}")
    if year:
        components.append(str(year))
    return " ".join(components) if components else "Issue"


def _month_name(month: int, language: str) -> str:
    if not (0 < month < len(MONTH_TABLE[0])):
        return f"Month {month}"
    lang_code = language.split('_')[0].lower()
    locales = MONTH_TABLE[0][0]
    indices = [idx for idx, locale in enumerate(locales) if locale.lower().startswith(lang_code)]
    if not indices:
        indices = [0]
    idx = indices[0]
    row = MONTH_TABLE[0][month]
    if idx < len(row):
        return row[idx]
    return row[0]
