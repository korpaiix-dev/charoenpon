# 📸 Image Pool — เจริญพร FB Auto-Post

## วิธีใช้

### อัปรูปผ่าน Telegram
ส่งรูปมาที่ **@Patafood_bot** พร้อมข้อความ `#เจริญพร`
→ ระบบเก็บเข้า `queue/` อัตโนมัติ

### อัปรูปตรงบน Server  
วางไฟล์รูป (.jpg/.png/.webp) ไว้ใน:
```
/root/charoenpon/fb-manager/images/queue/
```

## โครงสร้าง

```
images/
├── queue/      ← รูปที่รอโพสต์ (ยังไม่ได้ใช้)
├── used/       ← รูปที่โพสต์แล้ว (ย้ายมาหลังใช้)
└── README.md
```

## Flow
1. บอสอัปรูปเข้า `queue/`
2. Auto-post สุ่มหยิบรูปจาก `queue/`
3. โพสต์แล้ว → ย้ายรูปไป `used/`
4. ถ้า `queue/` หมด → วนรูปจาก `used/` กลับมาใช้ใหม่
