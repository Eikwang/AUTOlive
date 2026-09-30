"""Tars 协议编解码器（标准 tarsjce 语义，虎牙弹幕用）

编码规则（com.qq.tars.protocol.tars 语义）：
- 字段头 1B：(tag << 4) | type；tag >= 15 时头 = (0xF0 | type) + 1B 真 tag
- 类型：0=int8 1=int16 2=int32 3=int64 4=float 5=double 6=string1(1B len)
  7=string4(4B len) 8=map 9=list 10=structBegin 11=structEnd 12=zero
- 零值 int 编码为 type12（无值字节）；SimpleList（byte 数组）type13：
  头后 1B 元素类型 + int 长度（tag0 标准编码）+ 数据
"""

import struct as _struct
from typing import Any, Dict, List, Tuple


class TarsError(ValueError):
    """Tars 编解码异常"""


TYPE_INT8 = 0
TYPE_INT16 = 1
TYPE_INT32 = 2
TYPE_INT64 = 3
TYPE_FLOAT = 4
TYPE_DOUBLE = 5
TYPE_STRING1 = 6
TYPE_STRING4 = 7
TYPE_MAP = 8
TYPE_LIST = 9
TYPE_STRUCT_BEGIN = 10
TYPE_STRUCT_END = 11
TYPE_ZERO = 12
TYPE_SIMPLELIST = 13


def _write_length(out: bytearray, n: int) -> None:
    """无 tag 的 int 长度写入（tarsjce 内部：最小整型编码）"""
    if n == 0:
        out.append(TYPE_ZERO)  # 头位置直接 zero 类型
    elif -128 <= n <= 127:
        out.append(TYPE_INT8)
        out.extend(_struct.pack(">b", n))
    elif -32768 <= n <= 32767:
        out.append(TYPE_INT16)
        out.extend(_struct.pack(">h", n))
    elif -2147483648 <= n <= 2147483647:
        out.append(TYPE_INT32)
        out.extend(_struct.pack(">i", n))
    else:
        out.append(TYPE_INT64)
        out.extend(_struct.pack(">q", n))


class TarsOutputStream:
    """标准 Tars 序列化（字段按 tag 写入）"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def _header(self, tag: int, type_: int) -> None:
        if tag < 15:
            self._buf.append((tag << 4) | type_)
        else:
            self._buf.append((0xF0) | type_)
            self._buf.append(tag)

    # ---- 基础类型 ----

    def write_int(self, value: int, tag: int) -> None:
        if value == 0:
            self._header(tag, TYPE_ZERO)
            return
        if -128 <= value <= 127:
            self._header(tag, TYPE_INT8)
            self._buf.extend(_struct.pack(">b", value))
        elif -32768 <= value <= 32767:
            self._header(tag, TYPE_INT16)
            self._buf.extend(_struct.pack(">h", value))
        elif -2147483648 <= value <= 2147483647:
            self._header(tag, TYPE_INT32)
            self._buf.extend(_struct.pack(">i", value))
        else:
            self._header(tag, TYPE_INT64)
            self._buf.extend(_struct.pack(">q", value))

    def write_string(self, value: str, tag: int) -> None:
        data = (value or "").encode("utf-8")
        if len(data) <= 255:
            self._header(tag, TYPE_STRING1)
            self._buf.append(len(data))
        else:
            self._header(tag, TYPE_STRING4)
            self._buf.extend(_struct.pack(">I", len(data)))
        self._buf.extend(data)

    def write_bytes(self, value: bytes, tag: int) -> None:
        """byte[]（SimpleList type13）：元素类型 byte + 长度（tag0 编码）+ 数据"""
        self._header(tag, TYPE_SIMPLELIST)
        self._buf.append(TYPE_INT8)  # 元素类型
        if not value:
            self._buf.append(TYPE_ZERO)  # 长度 0（tag0 zero）
            return
        # 长度作为 tag0 int 标准编码
        self.write_int(len(value), 0)
        self._buf.extend(value)

    def write_bool(self, value: bool, tag: int) -> None:
        self.write_int(1 if value else 0, tag)

    def write_short(self, value: int, tag: int) -> None:
        self.write_int(value, tag)

    def write_long(self, value: int, tag: int) -> None:
        self.write_int(value, tag)

    def write_float(self, value: float, tag: int) -> None:
        self._header(tag, TYPE_FLOAT)
        self._buf.extend(_struct.pack(">f", value))

    def write_double(self, value: float, tag: int) -> None:
        self._header(tag, TYPE_DOUBLE)
        self._buf.extend(_struct.pack(">d", value))

    def write_map(self, value: Dict[Any, Any], tag: int) -> None:
        self._header(tag, TYPE_MAP)
        _write_length(self._buf, len(value))
        for k, v in value.items():
            self._write_any(k)
            self._write_any(v)

    def write_list(self, value: List[Any], tag: int) -> None:
        self._header(tag, TYPE_LIST)
        _write_length(self._buf, len(value))
        for v in value:
            self._write_any(v)

    def _write_any(self, value: Any) -> None:
        """容器元素写入（tag=0）"""
        if isinstance(value, str):
            self.write_string(value, 0)
        elif isinstance(value, bool):
            self.write_int(1 if value else 0, 0)
        elif isinstance(value, int):
            self.write_int(value, 0)
        elif isinstance(value, float):
            self.write_double(value, 0)
        elif isinstance(value, (bytes, bytearray)):
            self.write_bytes(bytes(value), 0)
        elif isinstance(value, dict):
            self.write_map(value, 0)
        elif isinstance(value, (list, tuple)):
            self.write_list(list(value), 0)
        elif isinstance(value, TarsStruct):
            value.write_to(self)
        else:
            raise TarsError(f"不支持的容器元素类型: {type(value).__name__}")

    def write_struct_begin(self, tag: int) -> None:
        self._header(tag, TYPE_STRUCT_BEGIN)

    def write_struct_end(self) -> None:
        self._buf.append(TYPE_STRUCT_END)

    def to_bytes(self) -> bytes:
        return bytes(self._buf)


class TarsStruct:
    """结构体基类：子类实现 write_to/read_from（嵌套字段用 struct 包裹）"""

    def write_to(self, os: TarsOutputStream) -> None:
        raise NotImplementedError

    def read_from(self, is_: "TarsInputStream") -> None:
        raise NotImplementedError


class TarsInputStream:
    """标准 Tars 反序列化"""

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self._pos = 0

    # ---- 头与跳读 ----

    def _read_header(self) -> Tuple[int, int]:
        if self._pos >= len(self._buf):
            raise TarsError("读取越界（头部）")
        b = self._buf[self._pos]
        self._pos += 1
        type_ = b & 0x0F
        tag = (b & 0xF0) >> 4
        if tag == 15:
            if self._pos >= len(self._buf):
                raise TarsError("读取越界（扩展 tag）")
            tag = self._buf[self._pos]
            self._pos += 1
        return tag, type_

    def skip_to_tag(self, tag: int) -> bool:
        """跳到指定 tag 的字段（struct 内）；找不到返回 False（位置在 structEnd 或已越过字段）

        tarsjce 语义：当前字段 tag > 目标 tag 时回退返回 False（不清目标字段）——
        保证「按 tag 递增读 + 缺失字段跳过」的调用序列正确。
        """
        while self._pos < len(self._buf):
            save = self._pos
            cur_tag, type_ = self._read_header()
            if type_ == TYPE_STRUCT_END:
                self._pos = save
                return False
            if cur_tag == tag:
                self._pos = save
                return True
            if cur_tag > tag:
                self._pos = save
                return False
            self._skip_field(type_)
        return False

    def _skip_field(self, type_: int) -> None:
        if type_ == TYPE_INT8:
            self._pos += 1
        elif type_ == TYPE_INT16:
            self._pos += 2
        elif type_ in (TYPE_INT32, TYPE_FLOAT):
            self._pos += 4
        elif type_ in (TYPE_INT64, TYPE_DOUBLE):
            self._pos += 8
        elif type_ == TYPE_STRING1:
            self._pos += 1 + self._buf[self._pos]
        elif type_ == TYPE_STRING4:
            n = _struct.unpack_from(">I", self._buf, self._pos)[0]
            self._pos += 4 + n
        elif type_ == TYPE_ZERO:
            pass
        elif type_ in (TYPE_MAP, TYPE_LIST):
            n = self._read_int_no_tag()
            for _ in range(n):
                t, _ty = self._read_header()
                self._skip_field(_ty)
                t, _ty = self._read_header()
                self._skip_field(_ty)
        elif type_ == TYPE_SIMPLELIST:
            _elem = self._buf[self._pos]
            self._pos += 1
            n = self._read_int_no_tag()
            self._pos += n
        elif type_ == TYPE_STRUCT_BEGIN:
            while True:
                t, ty = self._read_header()
                if ty == TYPE_STRUCT_END:
                    break
                self._skip_field(ty)
        else:
            raise TarsError(f"无法跳过的类型: {type_}")

    def _read_int_no_tag(self) -> int:
        tag, type_ = self._read_header()
        return self._read_int_by_type(type_)

    def enter_struct(self) -> bool:
        """消费当前字段的 structBegin 头（配合 skip_to_tag 命中 tag 后调用）"""
        tag, type_ = self._read_header()
        return type_ == TYPE_STRUCT_BEGIN

    def skip_to_struct_end(self) -> None:
        """从当前位置（struct 内任意点）扫到 structEnd 并消费——
        用于部分读取 struct 字段后对齐到 struct 末尾（外层继续读后续 tag）"""
        while self._pos < len(self._buf):
            save = self._pos
            _tag, type_ = self._read_header()
            if type_ == TYPE_STRUCT_END:
                return
            self._skip_field(type_)

    def skip_struct_end(self) -> None:
        """跳过 structEnd 字节（struct 内容读取完毕后调用）"""
        if self._pos < len(self._buf):
            tag, type_ = self._read_header()
            if type_ != TYPE_STRUCT_END:
                self._pos -= 1  # 不是 end，回退（交由外层继续）

    def _read_int_by_type(self, type_: int) -> int:
        if type_ == TYPE_ZERO:
            return 0
        if type_ == TYPE_INT8:
            v = _struct.unpack_from(">b", self._buf, self._pos)[0]
            self._pos += 1
            return v
        if type_ == TYPE_INT16:
            v = _struct.unpack_from(">h", self._buf, self._pos)[0]
            self._pos += 2
            return v
        if type_ == TYPE_INT32:
            v = _struct.unpack_from(">i", self._buf, self._pos)[0]
            self._pos += 4
            return v
        if type_ == TYPE_INT64:
            v = _struct.unpack_from(">q", self._buf, self._pos)[0]
            self._pos += 8
            return v
        raise TarsError(f"非整型字段: type={type_}")

    # ---- 基础类型读取（tag 定位）----

    def read_int(self, tag: int, default: int = 0) -> int:
        if not self.skip_to_tag(tag):
            return default
        _tag, type_ = self._read_header()
        return self._read_int_by_type(type_)

    def read_string(self, tag: int, default: str = "") -> str:
        if not self.skip_to_tag(tag):
            return default
        _tag, type_ = self._read_header()
        if type_ == TYPE_STRING1:
            n = self._buf[self._pos]
            self._pos += 1
            v = self._buf[self._pos : self._pos + n]
            self._pos += n
            return v.decode("utf-8", errors="replace")
        if type_ == TYPE_STRING4:
            n = _struct.unpack_from(">I", self._buf, self._pos)[0]
            self._pos += 4
            v = self._buf[self._pos : self._pos + n]
            self._pos += n
            return v.decode("utf-8", errors="replace")
        if type_ == TYPE_ZERO:
            return ""
        raise TarsError(f"非字符串字段: type={type_}")

    def read_bytes(self, tag: int, default: bytes = b"") -> bytes:
        """SimpleList byte[] 或 list<int8>"""
        if not self.skip_to_tag(tag):
            return default
        _tag, type_ = self._read_header()
        if type_ == TYPE_SIMPLELIST:
            elem_type = self._buf[self._pos]
            self._pos += 1
            n = self._read_int_no_tag()
            v = self._buf[self._pos : self._pos + n]
            self._pos += n
            return bytes(v)
        if type_ == TYPE_ZERO:
            return b""
        raise TarsError(f"非字节数组字段: type={type_}")

    def read_list(self, tag: int, default: List[Any] = None) -> List[Any]:
        if default is None:
            default = []
        if not self.skip_to_tag(tag):
            return default
        _tag, type_ = self._read_header()
        if type_ not in (TYPE_LIST, TYPE_MAP):
            if type_ == TYPE_ZERO:
                return default
            raise TarsError(f"非列表字段: type={type_}")
        n = self._read_int_no_tag()
        items: List[Any] = []
        for _ in range(n):
            items.append(self._read_any())
        return items

    def _read_any(self) -> Any:
        _tag, type_ = self._read_header()
        if type_ in (TYPE_ZERO, TYPE_INT8, TYPE_INT16, TYPE_INT32, TYPE_INT64):
            return self._read_int_by_type(type_)
        if type_ == TYPE_STRING1:
            n = self._buf[self._pos]
            self._pos += 1
            v = self._buf[self._pos : self._pos + n]
            self._pos += n
            return v.decode("utf-8", errors="replace")
        if type_ == TYPE_STRING4:
            n = _struct.unpack_from(">I", self._buf, self._pos)[0]
            self._pos += 4
            v = self._buf[self._pos : self._pos + n]
            self._pos += n
            return v.decode("utf-8", errors="replace")
        raise TarsError(f"容器元素不支持类型: {type_}")
