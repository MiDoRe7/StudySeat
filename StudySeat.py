import os
import re
import sys
import math
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


def check_startup() -> Store:
    # 1. 경로 확인
    if not os.path.isdir(BASE_DIR):
        print('error: 주 실행 파일의 경로를 확인할 수 없습니다. 프로그램을 종료합니다.')
        sys.exit()

    # 2. 파일 존재 및 권한 확인
    files = [USERS_FILE, INFO_FILE, LOG_FILE]
    for file in files:
        path = data_path(file)
        if not os.path.isfile(path): # 파일이 없는 경우
            print("warning: 데이터 파일이 없습니다: [%s]" % file)
            try:
                write_lines(path, [])
                print("빈 데이터 파일을 새로 생성했습니다: [%s]" % file)
            except OSError:
                print("error: 데이터 파일을 생성하지 못했습니다: [%s] 프로그램을 종료합니다." % file)
                sys.exit()
        else: # 파일이 존재하는 경우
            if not os.access(path, os.R_OK | os.W_OK):
                print("error: 데이터 파일에 대한 읽기 및 쓰기 권한이 없습니다: [%s] 프로그램을 종료합니다." % file)
                sys.exit()

    # 3. 파일별 검사
    users, last_dt, seats, logs = check_each_file()

    # 4. 좌석 현황 파일이 빈 파일인 경우 초기화
    if not seats: 
        seats = [SeatRecord(SeatNo(i)) for i in range(SEAT_TOTAL)]
        write_lines(data_path(INFO_FILE), [fmt_datetime(None)] + [rec.to_line() for rec in seats])
        print("빈 좌석 현황 파일을 초기화했습니다")
        
    store = Store(users, seats, logs, last_dt)
    
    # 5. 파일 간 검사
    check_cross_files(store)
    return store

def check_each_file() -> Tuple[List[UserRecord], Optional[datetime], List[SeatRecord], List[LogRecord]]:
    files = [USERS_FILE, INFO_FILE, LOG_FILE]
    has_error = False
    for file in files:
        path = data_path(file)
        try:
            lines = read_lines(path)
            
        except(UnicodeDecodeError):
            print("error: 데이터 파일을 UTF-8로 읽을 수 없습니다: [%s]" % file)
            has_error = True
            continue

        if file == files[0]:
            users = check_users_file(lines)
            if users is None:
                has_error = True
        elif file == files[1]:
            last_dt, seats = check_info_file(lines)
            if last_dt is None and seats is None:
                has_error = True
        else:
            logs = check_log_file(lines)
            if logs is None:
                has_error = True

    if has_error:
        sys.exit()

    return users, last_dt, seats, logs

def check_users_file(lines:List[str]) -> Optional[List[UserRecord]]:
    user_records = []
    bad = set()
    phones = {}
    seat_nos = {}
    for i, line in enumerate(lines):
        try:
            user_rec = UserRecord.from_line(line)
            user_records.append(user_rec)
            phones.setdefault(user_rec.phone, []).append(i)
            if user_rec.has_seat:
                seat_nos.setdefault(user_rec.seat_no, []).append(i)
        except(ElemSyntaxError):
            bad.add(i)            

    # 전화번호의 유일성
    for idxs in phones.values():
        if len(idxs) > 1:
            bad.update(idxs)

    # 좌석의 유일성
    for idxs in seat_nos.values():
        if len(idxs) > 1:
            bad.update(idxs)
    
    if len(bad) != 0:
        print('error: 데이터 파일에 규칙에 맞지 않는 행이 있습니다: [%s]' % USERS_FILE)
        for i in sorted(bad):
            print(lines[i])
        return None

    return user_records

def check_info_file(lines:List[str]) -> Tuple[Optional[datetime], Optional[List[SeatRecord]]]:
    seat_records = []
    # 행 개수 검사
    if len(lines) not in (0, SEAT_TOTAL + 1):
        print("error: 좌석 현황 파일의 행 개수가 올바르지 않습니다: %d" % len(lines))
        return None, None

    bad = set() # 틀린 행 번호를 모아둘 곳
    phones = {} # 전화번호 -> 행 번호 목록
    last_dt = None
    
    for i, line in enumerate(lines):
        try:
            if i == 0:
                last_dt = load_last_dt(line)
            else:
                rec = SeatRecord.from_line(line)
                seat_records.append(rec)
                if rec.seat.num + 1 != i:   # 좌석번호가 순서에 맞지 않을 때
                    bad.add(i)
                if rec.phone is not None: # 빈 좌석이 아니면
                    phones.setdefault(rec.phone.store(), []).append(i)
        except(ElemSyntaxError):
            bad.add(i)

    for v in phones.values():
        if len(v) >= 2:
            bad.update(v)    

    # 규칙에 맞지 않는 행 출력
    if len(bad) != 0:
        print('error: 데이터 파일에 규칙에 맞지 않는 행이 있습니다: [%s]' % INFO_FILE)
        for i in sorted(bad):
            print(lines[i])
        return None, None

    
    return last_dt, seat_records

def check_log_file(lines:List[str]) -> Optional[List[LogRecord]]:
    log_records = []
    bad = set()
    for i, line in enumerate(lines):
        try:
            log_records.append(LogRecord.from_line(line))
        except(ElemSyntaxError):
            bad.add(i)

    if len(bad) != 0:
        print('error: 데이터 파일에 규칙에 맞지 않는 행이 있습니다: [%s]' % LOG_FILE)
        for idx in sorted(bad):
            print(lines[idx])
        return None

    return log_records

def check_cross_files(store: Store) -> bool:
    error = 0
    # 회원에서 좌석으로
    for i, user_rec in enumerate(store.users):
        if user_rec.has_seat:
            seat_no = user_rec.seat_no
            if not store.seat_of(seat_no).phone ==  user_rec.phone:
                error += 1
                print("error: 데이터 파일 간 내용이 일치하지 않습니다. 프로그램을 종료합니다.")
                print("[%s] %d행: %s" % (USERS_FILE, i + 1, user_rec.to_line()))
                print("[%s] %d행: %s" % (INFO_FILE, seat_no.num + 2, store.seat_of(seat_no).to_line()))


    # 좌석에서 회원으로
    for i, seat_rec in enumerate(store.seats):
        if not seat_rec.is_empty:
            user_rec = store.find_user(seat_rec.phone)
            if user_rec is None or not user_rec.has_seat or seat_rec.seat != user_rec.seat_no:
                error += 1
                print("error: 데이터 파일 간 내용이 일치하지 않습니다. 프로그램을 종료합니다.")
                print("[%s] %d행: %s" % (INFO_FILE, i + 2, seat_rec.to_line()))
                if user_rec is not None:
                    print("[%s] %d행: %s" % (USERS_FILE, store.users.index(user_rec) + 1, user_rec.to_line()))

    # 예약 이력의 시점
    last_dt = store.last_dt
    if last_dt is None and len(store.logs) != 0:    # 최종 기록 일시가 기본값인데 예약 이력 파일에 레코드가 하나 이상 존재하는 경우
        error += 1
        print("error: 데이터 파일 간 내용이 일치하지 않습니다. 프로그램을 종료합니다.")
        print("[%s] %d행: %s" % (INFO_FILE, 1, fmt_datetime(last_dt)))
        for i, log_rec in enumerate(store.logs):
            print("[%s] %d행: %s" % (LOG_FILE, i + 1, log_rec.to_line()))

    elif last_dt is not None:
        bad_logs = {idx + 1:log_rec for idx, log_rec in enumerate(store.logs) if log_rec.start > last_dt}
        if len(bad_logs) > 0:
            error += 1
            print("error: 데이터 파일 간 내용이 일치하지 않습니다. 프로그램을 종료합니다.")
            print("[%s] %d행: %s" % (INFO_FILE, 1, fmt_datetime(last_dt)))
            for idx in bad_logs:
                print("[%s] %d행: %s" % (LOG_FILE, idx, bad_logs[idx].to_line()))

    if error > 0:
        sys.exit()

    return True

# store는 save()와의 호출 규약상 받기만 하고, 검사는 디스크에서 다시 읽은 내용으로 함
def check_after_write(store: Store) -> None:
    # 3. 파일별 검사 (디스크에 저장된 내용을 다시 읽음)
    users, last_dt, seats, logs = check_each_file()

    # 5. 파일 간 검사 (다시 읽은 내용 기준)
    check_cross_files(Store(users, seats, logs, last_dt))



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
        if candidate > datetime(9998, 12, 31, 23, 59):
            print("error: 입력가능한 날짜의 상한은 9998-12-31일 입니다.")
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
        print("error: 전화번호 혹은 비밀번호가 형식에 맞지 않습니다.")
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
            print()
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
            print("error: 전화번호와 비밀번호를 형식에 맞게 입력하시오.")
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


# ㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡ 부 프롬프트 보조 함수 ㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡ
def _no_args(args: List[str]) -> bool:
    if args:
        print("error: 명령어만을 입력하시오.")
        return False
    return True

def _check_penalty(store: Store) -> bool:
    if store.me.in_penalty(store.now):
        print(f"warning: 본 회원은 현재 페널티가 부과되어 있습니다. "
              f"페널티 종료 일시: {fmt_datetime(store.me.penalty_end)}")
        return False
    return True

def _one_word(s: str) -> Optional[str]:
    tokens = tokenize(s)
    if len(tokens) != 1 or not tokens[0].isprintable():
        return None
    return tokens[0]

def print_seat_map(store: Store, select: bool) -> None:
    lo, hi = (0, 40) if select else (40, SEAT_TOTAL)
    print("지정석 사용 현황 (좌상단부터 S00~S39)" if select
          else "자유석 이용 현황 (좌상단부터 F40~F99)")
    for row in range(lo, hi, 10):
        print("".join("O" if store.seats[i].is_empty else "X" for i in range(row, row + 10)))
    print("(O: 이용 가능 좌석 / X: 이용 불가 좌석)")

# ㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡ 부 프롬프트(충전/예약/변경) ㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡㅡ
def sub_add_time(store: Store, new_free=False) -> None:    #충전 프롬프트 분기 함수. 회원의 좌석 보유 여부를 확인 
    if store.me.has_select_seat:
        _add_days(store)
    else:
        _add_hours(store, new_free)

def _add_hours(store: Store, new_free: bool = False) -> None:    #자유석 시간 추가
    me = store.me
    while True:
        print("이용 서비스: 자유석")
        print(f"잔여 시간: {me.remain}시간")
        word = _one_word(read_sub("추가할 시간을 입력하시오 : "))
        try:
            hours = parse_amount(word or "")
        except ElemSyntaxError:
            hours = None
        if hours is None or hours > CHARGE_MAX:
            print(f"error: 충전 시간 입력 범위: 0 < 충전 시간 < {CHARGE_MAX + 1}")
            continue
        if me.remain + hours > REMAIN_MAX:
            print(f"error: 최대 잔여 시간은 {REMAIN_MAX}시간 입니다.")
            continue
        me.remain += hours
        if new_free:
            me.seat = "F"
        store.save()
        print("시간 충전이 완료되었습니다.")
        print(f"잔여 시간: {me.remain}시간")
        return

def _add_days(store: Store):    #지정석 시간 연장
    me, rec = store.me, store.seat_of(store.me.seat_no)
    limit = timedelta(days=SELECT_LIMIT_DAYS)
    while True:
        print("이용 서비스: 지정석")
        print(f"지정석 이용 종료 일시: {fmt_datetime(rec.end)}")
        word = _one_word(read_sub("추가할 기간(일 단위)을 입력하시오 : "))
        try:
            days = parse_amount(word or "")
        except ElemSyntaxError:
            print(f"error: 충전 기간은 1 이상의 정수로 입력하시오.")
            continue
        if days > SELECT_LIMIT_DAYS or (rec.end - store.now) + timedelta(days=days) > limit:
            print(f"error: 지정석 이용 종료 일시는 현재 일시({fmt_datetime(store.now)})로부터 {SELECT_LIMIT_DAYS}일을 초과할 수 없습니다")
            continue
        rec.end = rec.end + timedelta(days=days)
        store.add_log(me.seat_no, days)
        store.save()
        print("기간 연장이 완료되었습니다.")
        print(f"지정석 이용 종료 일시: {fmt_datetime(rec.end)}")
        return

def sub_select_service(store: Store):    #이용 서비스 선택
    while True:
        word = _one_word(read_sub("이용하실 서비스를 선택하세요(지정석의 경우 S, 자유석의 경우 F 입력): "))
        if word == "F" or word == "f":
            print("자유석 이용 선택됨")
            sub_add_time(store, new_free=True)
            return
        elif word == "S" or word == "s":
            print("지정석 이용 선택됨")
            sub_select_seat(store, "new")
            return
        else:
            print("error: 잘못된 입력입니다.")
            continue
            
def sub_select_seat(store: Store, mode: str) -> None:    #지정석 좌석 선택
    me = store.me
    while True:
        print_seat_map(store, True)
        word = _one_word(read_sub("이용하실 좌석을 입력하시오(S00~S39): "))
        if word is None:
            print("error: 하나의 좌석 번호를 입력하시오")
            continue
        try:
            seat = SeatNo.parse(word)
        except (ElemSyntaxError, ElemSemanticError):
            seat = None
        if seat is None or not seat.is_select:
            print("error: 지정석 좌석 번호를 입력하시오.(S00~S39)")
            continue
        target = store.seat_of(seat)
        if not target.is_empty:
            print("error: 이용이 불가능한 좌석입니다.")
            continue
        if mode == "new":
            target.phone = me.phone
            target.end = store.now + timedelta(days=1)
            me.seat = seat.store()
            store.add_log(seat, 1)
            store.save()
            print(f"지정석 {seat.store()}의 사용 신청이 완료되었습니다.")
            sub_add_time(store)
        else:
            old = store.seat_of(me.seat_no)
            target.phone, target.end = old.phone, old.end
            old.clear()
            me.seat = seat.store()
            days = math.ceil((target.end - store.now) / timedelta(days=1))
            store.add_log(seat, days)
            store.save()
            print(f"지정석이 {seat.store()}(으)로 변경되었습니다.")
            print(f"지정석 이용 종료 일시: {fmt_datetime(target.end)}")
        return

def sub_reserve_free(store: Store) -> None:    #부 프롬프트 4 : 자유석 예약
    me = store.me
    while True:
        print_seat_map(store, False)
        print(f"잔여 시간: {me.remain}시간")
        tokens = tokenize(read_sub(f"이용하실 좌석(F40~F99)과 이용하실 시간(1~{FREE_RESERVE_MAX})을 입력하시오: "))
        try:
            if len(tokens) != 2 or not all(t.isprintable() for t in tokens):
                raise ElemSyntaxError
            seat = SeatNo.parse(tokens[0])
            hours = parse_amount(tokens[1])
        except (ElemSyntaxError, ElemSemanticError):
            print(f"error: 하나의 좌석(F40~F99)과 이용시간(1~{FREE_RESERVE_MAX})을 입력하시오")
            continue
        if seat.is_select:
            print("error: 자유석 좌석 번호를 입력하시오.(F40~F99)")
            continue
        target = store.seat_of(seat)
        if not target.is_empty:
            print("error: 이용이 불가능한 좌석입니다.")
            continue
        if hours > me.remain:
            print("error: 잔여 시간이 부족합니다.")
            continue
        if hours > FREE_RESERVE_MAX:
            print(f"error: 최대 이용 시간은 {FREE_RESERVE_MAX}시간입니다.")
            continue
        target.phone = me.phone
        target.end = store.now + timedelta(hours=hours)
        me.seat = seat.store()
        me.remain -= hours
        store.add_log(seat, hours)
        store.save()
        print(f"{seat.store()}좌석의 예약이 완료되었습니다.")
        print(f"잔여 시간: {me.remain}시간")
        print(f"이용 종료 일시: {fmt_datetime(target.end)}")
        return

# ───────────────────────── 주 프롬프트 명령어 ─────────────────────────
# 공통 규약
#   - 시그니처는 (store, args), args는 명령어 뒤의 인자(단어) 목록
#   - 로그인 회원은 store.me, 현재 일시는 store.now
#   - 부 프롬프트 입력은 read_sub() 사용, QuitSignal은 잡지 말 것(주 프롬프트 루프가 처리)
#   - 데이터를 수정했으면 반환 전에 store.save() 호출
#   - 함수가 반환하면 주 프롬프트로 되돌아감

HELP_TEXT = {
    "login": {
        "args": "전화번호 비밀번호",
        "menu": "시스템에 로그인함",
        "summary": "시스템에 로그인하는 명령어",
        "action": "등록된 회원 정보로 로그인합니다.",
    },
    "signup": {
        "args": "전화번호 비밀번호 비밀번호확인",
        "menu": "시스템에 회원 가입함",
        "summary": "시스템에 회원 가입하는 명령어",
        "action": "회원 가입 후 로그인 프롬프트로 돌아갑니다.",
    },
    "help": {
        "args": "없거나 명령어 1개",
        "menu": "전체 도움말 혹은 명령어별 상세 도움말을 출력함",
        "summary": "도움말을 출력하는 명령어",
        "action": "전체 도움말 혹은 명령어별 상세 도움말을 출력합니다.",
    },
    "charge": {
        "args": "없음",
        "menu": "이용 시간 및 이용 기간을 충전함 이용권이 없는 경우 이용할 서비스를 선택함",
        "summary": "이용 시간 및 이용 기간을 충전하는 명령어",
        "action": "이용 시간 및 이용 기간을 충전합니다. 이용권이 없는 경우 이용할 서비스를 선택합니다.",
    },
    "reserve": {
        "args": "없음",
        "menu": "자유석을 예약함",
        "summary": "자유석을 예약하는 명령어",
        "action": "자유석을 예약합니다.",
    },
    "change": {
        "args": "없음",
        "menu": "지정석을 변경함",
        "summary": "지정석을 변경하는 명령어",
        "action": "이용 중인 지정석을 변경합니다.",
    },
    "log": {
        "args": "없음",
        "menu": "예약 내역을 출력함",
        "summary": "이용 내역을 조회하는 명령어",
        "action": "사용자의 이용 내역을 표시합니다.",
    },
    "report": {
        "args": "좌석 번호 1개",
        "menu": "불량 이용자에 대한 민원을 신청함",
        "summary": "불량 이용자에 대한 민원을 신청하는 명령어",
        "action": "다른 회원이 이용 중인 좌석에 대한 민원을 신청합니다.",
    },
    "quit": {
        "args": "없음",
        "menu": "프로그램을 종료함. 부 프롬프트에서는 주 프롬프트로 돌아감",
        "summary": "프로그램을 종료하거나 부 프롬프트에서 돌아가는 명령어",
        "action": "일시 입력, 로그인, 주 프롬프트에서는 프로그램을 종료합니다. 부 프롬프트에서는 주 프롬프트로 돌아갑니다.",
    },
}


def _print_command_help(groups: Tuple[str, ...]) -> None:
    print("— 명령어 및 인자에 대한 설명 —")
    print("명령어 / 인자 / 설명")

    for command in groups:
        info = HELP_TEXT[command]
        print(f"{command} / {info['args']} / {info['menu']}")

    print()


def print_main_help() -> None:
    _print_command_help(MAIN_GROUPS)


def print_login_help() -> None:
    _print_command_help(LOGIN_GROUPS)


def cmd_help(store: Store, args: List[str]) -> None:
    print_menu = print_login_help if store.me is None else print_main_help

    if len(args) > 1:
        print("error: 인자가 너무 많습니다. 최대 1개의 명령어만 입력하시오.")
        print_menu()
        return

    if not args:
        print_menu()
        return

    command = CMD_LOOKUP.get(args[0])
    if command is None:
        print(f'error: “{args[0]}” 명령어는 존재하지 않습니다.')
        print_menu()
        return

    info = HELP_TEXT[command]

    print(command)
    print(info["summary"])
    print(f"인자: {info['args']}")
    print(f"동작: {info['action']}")
    print()



def cmd_charge(store: Store, args: List[str]) -> None:
    # TODO(충전·예약·변경): 6.4, 부 프롬프트1~3
    if not _no_args(args) or not _check_penalty(store):
        return
    if store.me.has_no_ticket:
        sub_select_service(store)
    else:
        sub_add_time(store)


def cmd_reserve(store: Store, args: List[str]) -> None:
    # TODO(충전·예약·변경): 6.5, 부 프롬프트4
    if not _no_args(args):
        return
    if store.me.has_seat:
        print("error: 이미 좌석을 사용중입니다.")
        return
    if store.me.seat != "F":
        print("error: 자유석 서비스를 이용 중이 아닙니다.")
        return
    if not _check_penalty(store):
        return
    sub_reserve_free(store)


def cmd_change(store: Store, args: List[str]) -> None:
    # TODO(충전·예약·변경): 6.6, 부 프롬프트3
    if not _no_args(args):
        return
    if not store.me.has_select_seat:
        print("error: 지정석을 사용중이 아닙니다.")
        return
    sub_select_seat(store, "change")


def cmd_log(store: Store, args: List[str]) -> None:
    if not _no_args(args):
        return

    found = False
    for record in store.logs:
        if record.phone != store.me.phone:
            continue

        found = True
        print(f"사용 좌석: {record.seat.store()} /"
              f"사용 시작 일시: {fmt_datetime(record.start)} /"
              f"사용 시간: {record.amount}")

    if not found:
        print("이용 내역이 없습니다.")



def cmd_report(store: Store, args: List[str]) -> None:
    if not args:
        print("error: 민원을 신청 할 좌석 번호(S00~S39, F40~F99)를 입력하시오.")
        return

    if len(args) > 1:
        print("error: 인자가 너무 많습니다. 하나의 인자를 입력하시오.")
        return

    try:
        seat_no = SeatNo.parse(args[0])
    except (ElemSyntaxError, ElemSemanticError):
        print("error: 좌석 번호 형식(S00~S39, F40~F99)에 맞지 않습니다.")
        return

    seat = store.seat_of(seat_no)
    if seat.is_empty:
        print("error: 해당 좌석을 이용중인 사용자가 없습니다.")
        return

    if seat.phone == store.me.phone:
        print("error: 본인이 이용중인 좌석입니다.")
        return

    content = read_sub("해당 좌석에 대해 민원을 제기할 내용을 작성하시오: ")
    if not 1 <= len(content) <= REPORT_MAX_LEN:
        print(f"error: 민원의 내용은 최소 1자 최대 {REPORT_MAX_LEN}자 입니다.")
        return

    target = store.find_user(seat.phone)

    if target.warn == WARN_LIMIT - 1:
        penalty_end = store.now + timedelta(hours=PENALTY_HOURS)

        target.warn = 0
        target.penalty_end = penalty_end
        store.checkout(target)
    else:
        target.warn += 1

    store.save()
    print()
    print(f"{seat_no.store()}좌석에 대한 민원 ‘{content}’ 가 접수되었습니다. "
          "해당 회원에게 경고가 부여되었습니다.")
    print()


def cmd_quit(store: Store, args: List[str]) -> None:
    if args:
        print("error: 프로그램을 종료하려면 quit만을 입력하시오.")
        print()
        return

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
