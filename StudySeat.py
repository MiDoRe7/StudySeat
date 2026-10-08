import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple


# ───────────────────────── 상수 ─────────────────────────
USERS_FILE = "studyseat-users.txt"
INFO_FILE = "studyseat-info.txt"
LOG_FILE = "studyseat-log.txt"

SEAT_TOTAL = 100
CHARGE_MAX = 1000 # 1회 충전 시간 상한
REMAIN_MAX = 10000 # 잔여 시간 상한
SELECT_LIMIT_DAYS = 365 # 지정석 종료 일시 - 현재 일시 상한
FREE_RESERVE_MAX = 24 # 자유석 1회 예약 상한
WARN_LIMIT = 3 #경고 한도
PENALTY_HOURS = 72 #페널티 시간
REPORT_MAX_LEN = 2000
LOG_AMOUNT_MAX = 10000

DEFAULT_DT = "0000-00-00 00:00"
EMPTY_PHONE = "000-0000-0000"

# 명령어군
COMMANDS = {
    "signup": ["signup", "s"],
    "login": ["login"],
    "help": ["help", "h", "?"],
    "charge": ["charge", "c", "+"],
    "reserve": ["reserve", "r"],
    "change": ["change"],
    "log": ["log", "l"],
    "report": ["report"],
    "quit": ["quit", "q"],
}
CMD_LOOKUP = {w: std for std, ws in COMMANDS.items() for w in ws} # 명령어군에 있는 단어를 표준 명령어로 변환해주는 Dictionary
LOGIN_GROUPS = ("login", "signup", "help", "quit")
MAIN_GROUPS = ("help", "charge", "reserve", "change", "log", "report", "quit")


# ───────────────────────── 사용자 정의 예외 ────ㅡ──────────────
class ElemSyntaxError(Exception):
    """문법 형식 위배"""


class ElemSemanticError(Exception):
    """의미 규칙 위배"""


class QuitSignal(Exception):
    """부 프롬프트에서 quit 입력 -> 주 프롬프트 복귀"""


# ㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡ 데이터 요소 ㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡ
@dataclass(frozen=True)
class Phone:
    digits: str  # '-' 제거한 11자리

    _RE = re.compile(r"010[0-9]{8}|010-[0-9]{4}-[0-9]{4}")

    @classmethod
    def parse(cls, s: str) -> "Phone": # 사용자 입력 문자열을 Phone 객체로 변환
        if not cls._RE.fullmatch(s):
            raise ElemSyntaxError(s)
        return cls(s.replace("-", ""))

    def store(self) -> str: # 데이터 파일 저장 형식으로 변환(출력에도 사용 가능)
        d = self.digits
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"

    @staticmethod
    def is_stored_form(s: str) -> bool: # 파일에서 읽은 필드가 저장 형식인지 검사
        return re.fullmatch(r"010-[0-9]{4}-[0-9]{4}", s) is not None

class Password:  # 4.2 (표준형 없음, 문자열 그대로 사용)
    SPECIALS = "!@#$%^&*()" # 허용 특수문자

    @classmethod
    def is_valid(cls, s: str) -> bool: # 검사 함수
        if not 4 <= len(s) <= 20:
            return False
        if not re.fullmatch(r"[0-9A-Za-z!@#$%^&*()]+", s): # 문자열 전체가 숫자, 영문, SPECIALS로만 이루어졌는지 검사
            return False
        kinds = ( # 종류 검사
            any(c in "0123456789" for c in s)
            + any(c.isascii() and c.isalpha() for c in s)
            + any(c in cls.SPECIALS for c in s)
        )
        return kinds >= 2

@dataclass(frozen=True)
class SeatNo:
    num: int  # 0~99의 숫자만 보관(번호 기준으로 종류는 알아서 확정)

    _RE = re.compile(r"([SsFf]?)([0-9]{1,2})")

    @classmethod
    def parse(cls, s: str) -> "SeatNo": # 사용자 입력 문자열을 SeatNo 객체로 변환
        m = cls._RE.fullmatch(s)
        if not m:
            raise ElemSyntaxError(s)
        kind, num = m.group(1).upper(), int(m.group(2))
        if kind == "S" and num > 39:
            raise ElemSemanticError(s)
        if kind == "F" and num <= 39:
            raise ElemSemanticError(s)
        return cls(num)

    @classmethod
    def from_store(cls, s: str) -> "SeatNo": # 데이터 파일에서 읽는 용
        if not re.fullmatch(r"[SF][0-9]{2}", s):
            raise ElemSyntaxError(s)
        try:
            return cls.parse(s)
        except ElemSemanticError:
            raise ElemSyntaxError(s)

    @property
    def is_select(self) -> bool: #지정석 여부
        return self.num <= 39

    def store(self) -> str: #데이터 파일 저장 형식으로 변환(출력에도 사용 가능)
        return f"{'S' if self.is_select else 'F'}{self.num:02d}"


# 사용자 입력/데이터 파일용 표현식
_DATE_RE = r"([0-9]{4})([-/.])([0-9]{1,2})\2([0-9]{1,2})"
_DT_RE = re.compile(_DATE_RE + r" ([0-9]{1,2}):([0-9]{1,2})")
_DT_STORE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")

# 충전 시간/충전 기간 문법 검사 이후 int로 변환, 범위 검사는 호출 측에서 필요
def parse_amount(s: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", s):
        raise ElemSyntaxError(s)
    return int(s)


# 사용자 입력이 일시 입력 문법 + 의미 규칙에 맞으면 datetime 객체로 변환
def parse_datetime(s: str) -> datetime:
    m = _DT_RE.fullmatch(s)
    if not m:
        raise ElemSyntaxError(s)
    y, _, mo, d, h, mi = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi))
    except ValueError:
        raise ElemSemanticError(s)

# datetime 객체를 YYYY-MM-DD HH:MM으로 변환(파일 저장, 출력용)
# None = 페널티 없음, 빈 좌석, 최종 기록 없음 등
def fmt_datetime(dt: Optional[datetime]) -> str:
    if dt is None:
        return DEFAULT_DT
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}"

# 데이터 파일 필드를 datetime으로 변환, 0000-00-00 00:00은 datetime으로 생성 불가하므로 None으로 변환
def load_datetime(s: str, allow_default: bool = False) -> Optional[datetime]:
    # 예약 이력에서는 기본값을 금지하고있어서 추가
    if allow_default and s == DEFAULT_DT:
        return None
    if not _DT_STORE_RE.fullmatch(s):
        raise ElemSyntaxError(s)
    try:
        return parse_datetime(s)
    except ElemSemanticError:
        raise ElemSyntaxError(s)

# 데이터파일의 숫자 필드(잔여 시간, 경고 횟수, 이용 시간) 검사 이후 정수로 변환, width는 자릿수, lo, hi는 범위
def _load_int(s: str, width: int, lo: int, hi: int) -> int:
    if not re.fullmatch(rf"[0-9]{{{width}}}", s) or not lo <= int(s) <= hi:
        raise ElemSyntaxError(s)
    return int(s)


# ───────────────────────── 공통 함수 ─────────────────────────ㅡ

def read_lines(path: str) -> List[str]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [l for l in re.split(r"[\r\n]+", f.read()) if l]


def write_lines(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(l + "\n" for l in lines))


def tokenize(s: str) -> List[str]:
    return s.split()


def load_last_dt(line: str) -> Optional[datetime]:
    (field,) = split_fields(line, 1)
    return load_datetime(field, allow_default=True)


# ───────────────────────── 레코드 ─────────────────────────ㅡㅡ
# 데이터 파일의 한 행을 탭 기준으로 나누고 공통 규칙 검사
def split_fields(line: str, n: int) -> List[str]:
    """5.1.2 / 5.1.3: 탭 1개 구분, 빈 필드·앞뒤 공백류 불가"""
    fields = line.split("\t")
    if len(fields) != n:
        raise ElemSyntaxError(line)
    for f in fields:
        if not f or f[0].isspace() or f[-1].isspace():
            raise ElemSyntaxError(line)
    return fields

# 회원 정보 파일의 회원 클래스
@dataclass
class UserRecord:
    phone: Phone
    password: str
    seat: str
    remain: int = 0
    warn: int = 0
    penalty_end: Optional[datetime] = None

    @classmethod
    def from_line(cls, line: str) -> "UserRecord": # 파일의 한 행을 객체로 변환
        ph, pw, seat, rem, warn, pen = split_fields(line, 6)
        if not Phone.is_stored_form(ph) or not Password.is_valid(pw):
            raise ElemSyntaxError(line)
        if seat not in ("F", "N"):
            SeatNo.from_store(seat)
        rec = cls(Phone.parse(ph), pw, seat,
                  _load_int(rem, 5, 0, REMAIN_MAX),
                  _load_int(warn, 1, 0, WARN_LIMIT - 1),
                  load_datetime(pen, allow_default=True))
        if not rec.is_valid_record:
            raise ElemSyntaxError(line)
        return rec

    def to_line(self) -> str: # 객체를 파일에 저장하기 위한 한 행으로 변환
        return "\t".join([self.phone.store(), self.password, self.seat,
                          f"{self.remain:05d}", str(self.warn),
                          fmt_datetime(self.penalty_end)])

    @property
    def has_seat(self) -> bool: # 좌석 배정중인지 여부
        return self.seat not in ("F", "N")

    @property
    def has_select_seat(self) -> bool:  # S00~S39
        return self.has_seat and self.seat_no.is_select

    @property
    def has_free_ticket(self) -> bool:  # "F" 또는 F40~F99
        return self.seat == "F" or (self.has_seat and not self.seat_no.is_select)

    @property
    def has_no_ticket(self) -> bool:  # "N"
        return self.seat == "N"

    @property
    def is_valid_record(self) -> bool: # 회원 레코드 한 줄에서 좌석 번호, 잔여 시간 조합이 맞는지 확인하는 함수, 회원 정보 저장 전 확인용
        if self.seat == "N":
            return self.remain == 0
        if self.seat == "F":
            return self.remain >= 1
        if self.has_select_seat:
            return self.remain == 0
        return True

    @property
    def seat_no(self) -> Optional[SeatNo]: # 배정된 좌석번호 반환, 없으면 None
        return SeatNo.from_store(self.seat) if self.has_seat else None

    def in_penalty(self, now: datetime) -> bool: # 페널티 상태인지 여부, 현재 일시를 입력받아 페널티 종료 일시와 비교
        return self.penalty_end is not None and self.penalty_end > now

# 좌석 현황 파일의 좌석 클래스
@dataclass
class SeatRecord:
    seat: SeatNo
    phone: Optional[Phone] = None       # None = 빈 좌석
    end: Optional[datetime] = None

    @classmethod
    def from_line(cls, line: str) -> "SeatRecord": # 파일의 한 행을 객체로 변환
        seat, ph, end = split_fields(line, 3)
        if ph == EMPTY_PHONE:
            phone = None
        elif Phone.is_stored_form(ph):
            phone = Phone.parse(ph)
        else:
            raise ElemSyntaxError(line)
        end_dt = load_datetime(end, allow_default=True)
        if (phone is None) != (end_dt is None):
            raise ElemSyntaxError(line)
        return cls(SeatNo.from_store(seat), phone, end_dt)

    def to_line(self) -> str: # 객체를 파일에 저장하기 위한 한 행으로 변환
        ph = self.phone.store() if self.phone else EMPTY_PHONE
        return "\t".join([self.seat.store(), ph, fmt_datetime(self.end)])

    @property
    def is_empty(self) -> bool: # 빈 좌석 여부
        return self.phone is None

    def clear(self) -> None: # 좌석을 빈 좌석으로 되돌림, 만료 처리, 강제 퇴실, 좌석 변경 시 사용
        self.phone, self.end = None, None

# 예약 이력 파일의 이용 기록 클래스
@dataclass
class LogRecord:
    phone: Phone
    seat: SeatNo
    start: datetime
    amount: int      # 지정석: 일, 자유석: 시간

    @classmethod
    def from_line(cls, line: str) -> "LogRecord":
        ph, seat, start, amt = split_fields(line, 4)
        if not Phone.is_stored_form(ph):
            raise ElemSyntaxError(line)
        return cls(Phone.parse(ph), SeatNo.from_store(seat),
                   load_datetime(start), _load_int(amt, 5, 1, LOG_AMOUNT_MAX))

    def to_line(self) -> str:
        return "\t".join([self.phone.store(), self.seat.store(),
                          fmt_datetime(self.start), f"{self.amount:05d}"])


# ───────────────────────── 공용 상태 ─────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def data_path(name: str) -> str: # 데이터 파일 이름 -> 주 실행 파일과 같은 경로의 전체 경로
    return os.path.join(BASE_DIR, name)


# 프로그램 전체가 공유하는 데이터, 모든 프롬프트 함수는 이 객체 하나를 인자로 받음
@dataclass
class Store:
    users: List[UserRecord]
    seats: List[SeatRecord]             # 인덱스 = 좌석 번호(0~99)
    logs: List[LogRecord]
    last_dt: Optional[datetime]         # 최종 기록 일시, None = 기록 없음
    now: Optional[datetime] = None      # 현재 일시(일시 입력 프롬프트에서 설정)
    me: Optional[UserRecord] = None     # 로그인 회원(로그인 프롬프트에서 설정)

    def find_user(self, phone: Phone) -> Optional[UserRecord]: # 전화번호로 회원 찾기, 없으면 None
        return next((u for u in self.users if u.phone == phone), None)

    def seat_of(self, seat: SeatNo) -> SeatRecord: # 좌석 번호로 좌석 레코드 찾기
        return self.seats[seat.num]

    def checkout(self, user: UserRecord) -> None: # 퇴실 처리(5.2.3, 5.3.3), 만료 처리와 강제 퇴실 공용
        self.seat_of(user.seat_no).clear()
        user.seat = "F" if user.has_free_ticket and user.remain >= 1 else "N"

    def expire(self) -> None: # 현재 일시 기준으로 이용 기간이 끝난 좌석을 모두 퇴실 처리
        for s in self.seats:
            if not s.is_empty and s.end <= self.now:
                self.checkout(self.find_user(s.phone))

    def add_log(self, seat: SeatNo, amount: int) -> None: # 로그인 회원의 예약 이력 추가(시작 일시 = 현재 일시)
        self.logs.append(LogRecord(self.me.phone, seat, self.now, amount))

    def save(self) -> None: # 세 파일 저장 후 5.6.2 재검사, 데이터를 수정한 작업의 마지막에 한 번 호출
        write_lines(data_path(USERS_FILE), [u.to_line() for u in self.users])
        write_lines(data_path(INFO_FILE),
                    [fmt_datetime(self.last_dt)] + [s.to_line() for s in self.seats])
        write_lines(data_path(LOG_FILE), [l.to_line() for l in self.logs])
        check_after_write(self)


# ───────────────────────── 무결성 검사 (담당: 무결성) ─────────────────────────
def check_startup() -> Store:
    # TODO(무결성): 5.6.1의 1~5단계로 교체
    # 임시 구현: 검사 없이 파일을 읽어 Store를 만들기만 함(다른 담당자 테스트용)
    for name in (USERS_FILE, INFO_FILE, LOG_FILE):
        if not os.path.exists(data_path(name)):
            write_lines(data_path(name), [])
    users = [UserRecord.from_line(l) for l in read_lines(data_path(USERS_FILE))]
    logs = [LogRecord.from_line(l) for l in read_lines(data_path(LOG_FILE))]
    info = read_lines(data_path(INFO_FILE))
    if info:
        return Store(users, [SeatRecord.from_line(l) for l in info[1:]], logs, load_last_dt(info[0]))
    store = Store(users, [SeatRecord(SeatNo(i)) for i in range(SEAT_TOTAL)], logs, None)
    store.save()
    return store


def check_after_write(store: Store) -> None:
    # TODO(무결성): 5.6.2 (5.6.1의 3단계, 5단계), 오류 시 출력 후 sys.exit()
    pass


# ───────────────────────── 입력 공용 ─────────────────────────
# 모든 부 프롬프트는 input() 대신 이 함수를 사용, quit 명령어군만 입력되면 QuitSignal 발생
def read_sub(msg: str) -> str:
    s = input(msg)
    tokens = tokenize(s)
    if len(tokens) == 1 and CMD_LOOKUP.get(tokens[0]) == "quit":
        raise QuitSignal()
    return s


# ───────────────────────── 일시 입력 / 로그인 프롬프트 (담당: 로그인·일시) ─────────────────────────
def _entry_text(s: str) -> str:
    """두 진입 프롬프트의 단어/횡공백류 문법 검사. 내부 공백은 보존한다."""
    if any(c in "\r\n" or (not c.isspace() and not c.isprintable()) for c in s):
        raise ElemSyntaxError(s)
    return s.strip()


def prompt_datetime(store: Store) -> None:
    """유효하고 최종 기록보다 늦은 일시를 수락한 경우에만 반환한다."""
    while True:
        try:
            s = _entry_text(input("현재 일시를 입력하시오: "))
            if CMD_LOOKUP.get(s) == "quit":
                sys.exit()
            candidate = parse_datetime(s)
        except ElemSyntaxError:
            print("error: 올바른 형식의 날짜와 시간을 입력하시오( YYYY-MM-DD HH:MM )")
            continue
        except ElemSemanticError:
            print("error: 실제 존재하는 일시를 입력하시오.")
            continue
        if store.last_dt is not None and candidate <= store.last_dt:
            print(f"error: 최종 기록 일시({fmt_datetime(store.last_dt)})보다 늦은 일시를 입력하시오.")
            continue
        store.now = candidate
        print(f"현재 일시: {fmt_datetime(candidate)}")
        return


def cmd_signup(store: Store, args: List[str]) -> None:
    """회원 정보를 저장한 뒤 로그인 프롬프트로 복귀한다. 자동 로그인은 하지 않는다."""
    format_error = "error: 전화번호와 비밀번호, 확인을 위해 비밀번호를 한번 더 입력하시오."
    if len(args) != 3:
        print(format_error)
        return
    phone_text, password, confirmation = args
    try:
        phone = Phone.parse(phone_text)
    except ElemSyntaxError:
        print(format_error)
        return
    if not Password.is_valid(password):
        print("error: 비밀번호는 4~20자로, 영문 대소문자·숫자·특수문자(!@#$%^&*())만 사용할 수 있으며 "
              "영문자·숫자·특수문자 중 두 종류 이상을 포함해야 합니다. 공백은 사용할 수 없습니다.")
        return
    if password != confirmation:
        print("error: 비밀번호와 비밀번호 확인이 일치하지 않습니다. 다시 한번 확인하시오.")
        return
    if store.find_user(phone) is not None:
        print("error: 이미 존재하는 회원입니다. 로그인을 진행하십시오.")
        return
    store.users.append(UserRecord(phone=phone, password=password, seat="N"))
    # 저장/사후 검사 실패는 전파한다. 성공 전에는 가입 완료를 안내하지 않는다.
    store.save()
    print(f"전화번호 {phone.store()}로 가입되었습니다. 로그인을 진행하십시오.")


def prompt_login(store: Store) -> None:
    """회원 가입 후에는 반복하고, 기존 회원 인증에 성공하면 반환한다."""
    while True:
        try:
            tokens = tokenize(_entry_text(input("로그인 혹은 회원가입을 진행하시오> ")))
        except ElemSyntaxError:
            print("error: login, signup, help, quit 중 하나를 입력하시오.")
            continue
        group = CMD_LOOKUP.get(tokens[0]) if tokens else None
        if group not in LOGIN_GROUPS:
            print("error: login, signup, help, quit 중 하나를 입력하시오.")
            continue
        args = tokens[1:]
        if group == "quit":
            if args:
                print("error: 프로그램을 종료하려면 quit만을 입력하시오.")
                continue
            sys.exit()
        if group == "help":
            cmd_help(store, args)
            continue
        if group == "signup":
            cmd_signup(store, args)
            continue
        if len(args) != 2:
            print("error: 전화번호와 비밀번호를 입력하시오.")
            continue
        try:
            phone = Phone.parse(args[0])
        except ElemSyntaxError:
            print("error: 전화번호와 비밀번호를 형식에 맞게 입력하시오.")
            continue
        if not Password.is_valid(args[1]):
            print("error: 전화번호와 비밀번호를 형식에 맞게 입력하시오.")
            continue
        user = store.find_user(phone)
        if user is None or user.password != args[1]:
            print("error: 존재하지 않는 회원이거나 잘못된 비밀번호입니다.")
            continue
        store.me = user
        print(f"login: {user.phone.store()}로 로그인 되었습니다.")
        return


# ───────────────────────── 주 프롬프트 명령어 ─────────────────────────
# 공통 규약
#   - 시그니처는 (store, args), args는 명령어 뒤의 인자(단어) 목록
#   - 로그인 회원은 store.me, 현재 일시는 store.now
#   - 부 프롬프트 입력은 read_sub() 사용, QuitSignal은 잡지 말 것(주 프롬프트 루프가 처리)
#   - 데이터를 수정했으면 반환 전에 store.save() 호출
#   - 함수가 반환하면 주 프롬프트로 되돌아감

def print_main_help() -> None:
    # TODO(도움말): 6절의 주 프롬프트 명령어 안내 화면
    print("— 명령어 및 인자에 대한 설명 —")


def print_login_help() -> None:
    # TODO(도움말): 6.2의 로그인 프롬프트 명령어 안내 화면
    print("— 명령어 및 인자에 대한 설명 —")


def cmd_help(store: Store, args: List[str]) -> None:
    # TODO(도움말): 6.3 (인자 0~1개, 명령어별 상세 도움말)
    # 로그인 프롬프트에서도 호출됨, store.me가 None이면 로그인 프롬프트
    print_login_help() if store.me is None else print_main_help()


def cmd_charge(store: Store, args: List[str]) -> None:
    # TODO(충전·예약·변경): 6.4, 부 프롬프트1~3
    print("미구현: charge")


def cmd_reserve(store: Store, args: List[str]) -> None:
    # TODO(충전·예약·변경): 6.5, 부 프롬프트4
    print("미구현: reserve")


def cmd_change(store: Store, args: List[str]) -> None:
    # TODO(충전·예약·변경): 6.6, 부 프롬프트3
    print("미구현: change")


def cmd_log(store: Store, args: List[str]) -> None:
    # TODO(조회·민원·종료): 6.7
    print("미구현: log")


def cmd_report(store: Store, args: List[str]) -> None:
    # TODO(조회·민원·종료): 6.8, 부 프롬프트5, 강제 퇴실은 store.checkout() 사용
    print("미구현: report")


def cmd_quit(store: Store, args: List[str]) -> None:
    # TODO(조회·민원·종료): 6.9 (인자가 있으면 오류 메시지)
    sys.exit()


MAIN_HANDLERS = {
    "help": cmd_help,
    "charge": cmd_charge,
    "reserve": cmd_reserve,
    "change": cmd_change,
    "log": cmd_log,
    "report": cmd_report,
    "quit": cmd_quit,
}


def prompt_main(store: Store) -> None:
    while True:
        tokens = tokenize(input("StudySeat > "))
        group = CMD_LOOKUP.get(tokens[0]) if tokens else None
        if group not in MAIN_GROUPS: # 빈 입력, 주 프롬프트에 없는 명령어
            print_main_help()
            continue
        try:
            MAIN_HANDLERS[group](store, tokens[1:])
        except QuitSignal: # 부 프롬프트에서 quit
            pass


# ───────────────────────── 시작 지점 ─────────────────────────
def main() -> None:
    store = check_startup()         # 5.6.1의 1~5단계
    prompt_datetime(store)          # 6단계
    store.last_dt = store.now       # 7단계
    store.expire()                  # 8단계
    store.save()
    prompt_login(store)
    prompt_main(store)


if __name__ == "__main__":
    main()
