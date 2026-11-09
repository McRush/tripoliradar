"""Unpack Microsoft EXEPACK'd DOS executables. Writes a normal MZ exe.
usage: unexepack.py IN.EXE OUT.EXE"""
import struct, sys

def unpack(d):
    h = list(struct.unpack_from('<14H', d, 0))
    hdr_size = h[4] * 16
    img = d[hdr_size:]
    cs, ip = h[11], h[10]
    ep = cs * 16
    f = struct.unpack_from('<9H', img, ep)
    if f[8] == 0x4252:   # has skip_len
        real_ip, real_cs, _, exe_size, real_sp, real_ss, dest_len, skip_len, _ = f
    else:
        real_ip, real_cs, _, exe_size, real_sp, real_ss, dest_len, _ = f[:8]; skip_len = 1
    packed_len = ep - (skip_len - 1) * 16
    buf = bytearray(max(dest_len * 16, packed_len))
    buf[:packed_len] = img[:packed_len]
    src = packed_len - 1
    while buf[src] == 0xff: src -= 1
    dst = dest_len * 16 - 1
    while True:
        cmd = buf[src]; src -= 1
        n = buf[src] << 8 | buf[src - 1]; src -= 2
        if cmd & 0xfe == 0xb0:
            fill = buf[src]; src -= 1
            for _ in range(n): buf[dst] = fill; dst -= 1
        elif cmd & 0xfe == 0xb2:
            for _ in range(n): buf[dst] = buf[src]; dst -= 1; src -= 1
        else:
            raise ValueError(f'bad cmd {cmd:#x}')
        if cmd & 1: break
    out = bytes(buf[:dest_len * 16])
    # relocation table follows the "Packed file is corrupt" message in the stub
    stub = img[ep:ep + exe_size]
    k = stub.find(b'Packed file is corrupt') + 22
    relocs = []
    for seg in range(16):
        cnt = struct.unpack_from('<H', stub, k)[0]; k += 2
        for _ in range(cnt):
            relocs.append((struct.unpack_from('<H', stub, k)[0], seg * 0x1000)); k += 2
    return out, relocs, (real_cs, real_ip, real_ss, real_sp), h

def build_mz(img, relocs, regs, h):
    cs, ip, ss, sp = regs
    rel_off = 0x1c
    hdr_len = (rel_off + 4 * len(relocs) + 15) // 16 * 16
    total = hdr_len + len(img)
    hdr = bytearray(hdr_len)
    struct.pack_into('<14H', hdr, 0, 0x5a4d, total % 512, (total + 511) // 512, len(relocs),
                     hdr_len // 16, h[5], h[6], ss, sp, 0, ip, cs, rel_off, 0)
    for i, (o, s) in enumerate(relocs):
        struct.pack_into('<HH', hdr, rel_off + 4 * i, o, s)
    return bytes(hdr) + img

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    img, relocs, regs, h = unpack(d)
    open(sys.argv[2], 'wb').write(build_mz(img, relocs, regs, h))
    print(f'{sys.argv[1]}: image {len(img)} bytes, {len(relocs)} relocs, CS:IP={regs[0]:04x}:{regs[1]:04x} SS:SP={regs[2]:04x}:{regs[3]:04x}')
