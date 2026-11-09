"""Tiny 16-bit disassembler helper for unpacked MZ images.
usage: dis.py EXE img_offset [count]"""
import sys, struct
from capstone import Cs, CS_ARCH_X86, CS_MODE_16
def load(path):
    d = open(path, 'rb').read()
    return d[struct.unpack_from('<H', d, 8)[0] * 16:]
def dis(img, off, n=40, seg_base=None):
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    sb = seg_base if seg_base is not None else off & ~0xffff
    for i, ins in enumerate(md.disasm(img[off:off + n * 8], off - sb)):
        if i >= n: break
        print(f'{sb + ins.address:06x} {ins.address:04x}: {ins.bytes.hex():14} {ins.mnemonic} {ins.op_str}')
if __name__ == '__main__':
    img = load(sys.argv[1])
    dis(img, int(sys.argv[2], 16), int(sys.argv[3]) if len(sys.argv) > 3 else 40)
