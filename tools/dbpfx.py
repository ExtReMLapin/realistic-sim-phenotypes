import struct, zlib, sys
def entries(p):
    f=open(p,'rb'); h=f.read(96)
    cnt=struct.unpack_from('<I',h,36)[0]; isz=struct.unpack_from('<I',h,44)[0]; ioff=struct.unpack_from('<Q',h,64)[0] or struct.unpack_from('<I',h,40)[0]
    f.seek(ioff); d=f.read(isz); flags=struct.unpack_from('<I',d,0)[0]; pos=4; const={}
    for i,k in enumerate(('t','g','ih')):
        if flags&(1<<i): const[k]=struct.unpack_from('<I',d,pos)[0]; pos+=4
    for _ in range(cnt):
        v={}
        for k in ('t','g','ih'):
            if k in const: v[k]=const[k]
            else: v[k]=struct.unpack_from('<I',d,pos)[0]; pos+=4
        il,off,fs,ms,comp,_c=struct.unpack_from('<IIIIHH',d,pos); pos+=20
        yield f,v['t'],v['g'],(v['ih']<<32)|il,off,fs&0x7fffffff,ms,comp
def read(f,off,fs,ms,comp):
    f.seek(off); raw=f.read(fs)
    if comp==0x5a42: return zlib.decompress(raw)
    if comp==0: return raw
    if comp==0xffff: return refpack(raw)
    return None
def refpack(data):
    # EA RefPack
    pos=0; flags=data[0]
    pos=2; size_bytes=4 if flags&0x80 else 3
    usize=int.from_bytes(data[pos:pos+size_bytes],'big'); pos+=size_bytes
    if flags&0x01: pos+=size_bytes
    out=bytearray()
    while pos<len(data):
        b0=data[pos]
        if b0<0x80:
            b1=data[pos+1]; pos+=2
            plain=b0&3; dist=((b0&0x60)<<3)+b1+1; cnt=((b0&0x1c)>>2)+3
        elif b0<0xc0:
            b1,b2=data[pos+1],data[pos+2]; pos+=3
            plain=b1>>6; dist=((b1&0x3f)<<8)+b2+1; cnt=(b0&0x3f)+4
        elif b0<0xe0:
            b1,b2,b3=data[pos+1],data[pos+2],data[pos+3]; pos+=4
            plain=b0&3; dist=((b0&0x10)<<12)+(b1<<8)+b2+1; cnt=((b0&0x0c)<<6)+b3+5
        elif b0<0xfc:
            plain=((b0&0x1f)<<2)+4; pos+=1; out+=data[pos:pos+plain]; pos+=plain; continue
        else:
            plain=b0&3; pos+=1; out+=data[pos:pos+plain]; break
        out+=data[pos:pos+plain]; pos+=plain
        for _ in range(cnt): out.append(out[-dist])
    return bytes(out)
