const fs=require('fs'),path=require('path'),assert=require('assert'),crypto=require('crypto');
const table=Array.from({length:256},(_,i)=>{let c=i<<24;for(let j=0;j<8;j++)c=(c<<1)^((c>>>31)?0xf4acfb13:0);return c>>>0});
function crc(b){let c=0;for(let i=8;i<b.length;i++)c=((c<<8)^table[(c>>>24)^b[i]])>>>0;return c;}
const src='outputs/xreal-firmware-cache/type4_1.9.1.2026091417562520260914175902.bin';
const original=fs.readFileSync(src);assert.equal(crc(original),original.readUInt32LE(0));assert.equal(original.readUInt32LE(4),original.length-8);
const zip=fs.readFileSync('work/uniform-fonts/pilot-uniform.zip');const b=Buffer.concat([original.subarray(0,64),zip.map(x=>x^0x69)]);
b.writeUInt32LE(b.length-8,4);b.writeUInt32LE(crc(b),0);assert.equal(crc(b),b.readUInt32LE(0));assert.deepEqual(b.subarray(8,64),original.subarray(8,64));
const dst='outputs/xreal-uniform-fonts/XREAL_One_Pro_Pilot_RU.bin';fs.writeFileSync(dst,b);
const manifest={file:path.basename(dst),bytes:b.length,sha256:crypto.createHash('sha256').update(b).digest('hex'),container_crc:b.readUInt32LE(0).toString(16),project_code:b.readUInt32LE(8),component_type:b.readUInt32LE(12),original_header_identity_preserved:true,usb_endpoint_bytes:512,usb_chunk_bytes:490,commands:{start:4628,transmit:4629,finish:4630},source:src};
fs.writeFileSync('outputs/xreal-uniform-fonts/package-manifest.json',JSON.stringify(manifest,null,2));console.log(manifest);
