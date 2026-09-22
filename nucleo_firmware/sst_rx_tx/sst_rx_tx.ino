// sst_rx_tx.ino — SST01 serial-command TX/RX firmware
// Based on proven sst_fixlen.ino; adds serial commands for mode switching.
//
// Serial commands (115200 baud, \n terminated):
//   TX:<text>   — transmit <text> (max 63 bytes), report ipksent
//   RX          — enter RX mode, poll for packet, report received bytes
//   IDLE        — return to idle (both TXON=H RXON=H)
//   STATE:<hex> — set 07h byte for RX (default 0x11, override to try 0x0D etc.)
//   PING        — reply "PONG" (health check)
//
// Wiring (Nucleo G474RE Arduino header):
//   D10=CS(PB6) D11=SCK(PA7) D13=MOSI(PA5) D12=MISO(PA6)
//   D9=SDN1(PA8) D8=SDN2  D3=RXON(PB3) D4=TXON(PB5) D2=nIRQ(PA10)

#define CS   10
#define SCK  11
#define MOSI 13
#define MISO 12
#define SDN  9
#define SDN2 8
#define RXON 3
#define TXON 4
#define IRQ  2

// --- Bit-bang SPI (proven in sst_fixlen.ino) ---
uint8_t xf(uint8_t o){
  uint8_t in=0;
  for(int i=7;i>=0;i--){
    digitalWrite(MOSI,(o>>i)&1);
    delayMicroseconds(50);
    digitalWrite(SCK,1);
    delayMicroseconds(50);
    in|=(digitalRead(MISO)&1)<<i;
    digitalWrite(SCK,0);
    delayMicroseconds(50);
  }
  return in;
}
uint8_t rd(uint8_t a){
  digitalWrite(CS,0);delayMicroseconds(20);
  xf(a&0x7F);uint8_t v=xf(0);
  digitalWrite(CS,1);delayMicroseconds(20);
  return v;
}
void wr(uint8_t a,uint8_t v){
  digitalWrite(CS,0);delayMicroseconds(20);
  xf(0x80|a);xf(v);
  digitalWrite(CS,1);delayMicroseconds(20);
}

// --- Register config (exact copy from proven sst_fixlen.ino) ---
struct Reg{uint8_t a;uint8_t v;};
Reg cfg[]={
  {0x1C,0x81},{0x1D,0x40},{0x20,0x64},{0x21,0x01},{0x22,0x47},{0x23,0xAE},{0x24,0x06},{0x25,0x27},
  {0x2A,0x50},{0x30,0xAC},{0x32,0x8C},{0x33,0x0A},{0x34,0x08},{0x35,0x2A},{0x36,0x2D},{0x37,0xD4},
  {0x3E,0x07},
  {0x6E,0x4E},{0x6F,0xA4},
  {0x70,0x2C},{0x71,0x23},{0x72,0x08},
  {0x75,0x53},{0x76,0x64},{0x77,0x00},
  {0x6D,0x18},
  {0x08,0x00},
};
#define NCFG (sizeof(cfg)/sizeof(cfg[0]))

// --- Serial input buffer ---
char cmdbuf[128];
volatile uint8_t cmdlen=0;
uint8_t cmdready=0;

void setup(){
  Serial.begin(115200); delay(2000);
  pinMode(CS,OUTPUT);  digitalWrite(CS,1);
  pinMode(SCK,OUTPUT); digitalWrite(SCK,0);
  pinMode(MOSI,OUTPUT);digitalWrite(MOSI,0);
  pinMode(MISO,INPUT);
  pinMode(SDN,OUTPUT); pinMode(SDN2,OUTPUT);
  pinMode(RXON,OUTPUT);pinMode(TXON,OUTPUT);
  pinMode(IRQ,INPUT_PULLUP);
  digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);

  // POR
  digitalWrite(SDN,HIGH); digitalWrite(SDN2,HIGH); delay(500);
  digitalWrite(SDN,LOW);  digitalWrite(SDN2,LOW);  delay(500);
  Serial.println("SST01_RXTX POR done");

  // SWRESET
  wr(0x07,0x80); delay(300); rd(0x03); rd(0x04);
  Serial.print("DT="); Serial.println(rd(0x00),HEX);

  // Program registers (same as sst_fixlen)
  for(uint8_t i=0;i<NCFG;i++){wr(cfg[i].a,cfg[i].v); delay(5);}
  Serial.println("Regs programmed");

  // Idle
  digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
  wr(0x07,0x01); // READY
  delay(100);
  Serial.println("READY. Commands: TX:<text>  RX  IDLE  STATE:<hex>  PING");
}

// --- Main loop ---
void loop(){
  // Accumulate serial input
  while(Serial.available()){
    char c=Serial.read();
    if(c=='\n'||c=='\r'){
      if(cmdlen>0){cmdbuf[cmdlen]=0; cmdready=1;}
    }else if(cmdlen<sizeof(cmdbuf)-1){
      cmdbuf[cmdlen++]=c;
    }
  }

  if(!cmdready) return;
  cmdready=0;

  // --- Parse command ---
  if(strncmp(cmdbuf,"TX:",3)==0){
    handle_tx(cmdbuf+3, cmdlen-3);
  }else if(strcmp(cmdbuf,"RX")==0){
    handle_rx();
  }else if(strcmp(cmdbuf,"IDLE")==0){
    idle();
    Serial.println("IDLE");
  }else if(strncmp(cmdbuf,"STATE:",6)==0){
    uint8_t v=(uint8_t)strtol(cmdbuf+6,NULL,16);
    wr(0x07,v);
    Serial.print("07h="); Serial.println(v,HEX);
  }else if(strcmp(cmdbuf,"PING")==0){
    Serial.println("PONG");
  }else{
    Serial.print("ERR: unknown cmd [");
    Serial.print(cmdbuf); Serial.println("]");
  }
  cmdlen=0;
}

// --- TX handler ---
void handle_tx(const char* text, uint8_t len){
  if(len==0){Serial.println("ERR empty");return;}
  if(len>63){Serial.println("ERR too long (max 63)");return;}

  // Set fixed packet length (0x3E config=0x07 by default; only needed for >7-byte texts)
  // wr(0x3E,len); delay(5);

  // Clear stale interrupts, then reset FIFO pointers (TX + RX)
  rd(0x03); rd(0x04);

  // Clear FIFO: reset TX then RX FIFO pointers
  wr(0x08,0x01); delay(5); wr(0x08,0x00); delay(5);
  wr(0x08,0x02); delay(5); wr(0x08,0x00); delay(5);
  rd(0x03); rd(0x04); // clear any ffem/ffs error flags

  // Burst-fill TX FIFO
  digitalWrite(CS,0); delayMicroseconds(20);
  xf(0x80|0x7F); // FIFO burst-write address
  for(uint8_t i=0;i<len;i++) xf((uint8_t)text[i]);
  digitalWrite(CS,1); delayMicroseconds(20);

  Serial.print("TX["); Serial.print(len); Serial.print("] ");
  Serial.write(text,len); Serial.println();

  // RF switch: TX mode (TXON=L, RXON=H) — per AGENTS.md §4
  digitalWrite(RXON,HIGH); digitalWrite(TXON,LOW);

  // Enter TX state
  wr(0x07,0x09);

  // Poll ipksent (03h bit D2 = 0x04, proven in sst_fixlen.ino)
  unsigned long t0=millis();
  while(millis()-t0<500){
    uint8_t st=rd(0x03);
    if(st & 0x04){
      Serial.print("ipksent st=0x"); Serial.println(st,HEX);
      rd(0x03); rd(0x04); // clear
      break;
    }
    delay(10);
  }
  if(millis()-t0>=500){
    Serial.println("TIMEOUT ipksent");
    Serial.print(" 03h="); Serial.print(rd(0x03),HEX);
    Serial.print(" 04h="); Serial.print(rd(0x04),HEX);
    Serial.print(" 05h="); Serial.print(rd(0x05),HEX);
    Serial.print(" 07h="); Serial.print(rd(0x07),HEX);
    Serial.print(" 08h="); Serial.println(rd(0x08),HEX);
  }

  idle();
  Serial.println("TX done");
}

// --- RX handler ---
void handle_rx(){
  Serial.println("RX enter");

  // RF switch: RX mode (TXON=H, RXON=L) — per AGENTS.md §4
  digitalWrite(TXON,HIGH); digitalWrite(RXON,LOW);

  // Clear RX FIFO
  wr(0x08,0x02); delay(5); wr(0x08,0x00); delay(5);

  // Enter RX state. Default 0x11 (rxon=D4 + xton=D0).
  // Override with STATE:<hex> if this doesn't work.
  wr(0x07,0x11);

  Serial.print("07h=0x11, polling ipkvalid...");
  Serial.print(" 03h="); Serial.print(rd(0x03),HEX);
  Serial.print(" 04h="); Serial.println(rd(0x04),HEX);

  // Poll for ipkvalid — try multiple candidate bits.
  // Si4432-family: ipkvalid could be 03h D1, D3, or 04h D2.
  // We poll all three and report which fires.
  uint8_t got=0;
  unsigned long t0=millis();
  while(millis()-t0<3000 && !got){
    uint8_t s3=rd(0x03);
    uint8_t s4=rd(0x04);
    if(s3 & 0x02){Serial.print("ipkvalid? 03h.D1 st3=0x");Serial.print(s3,HEX);Serial.print(" st4=0x");Serial.println(s4,HEX); got=1;}
    if(s3 & 0x08){Serial.print("ipkvalid? 03h.D3 st3=0x");Serial.print(s3,HEX);Serial.print(" st4=0x");Serial.println(s4,HEX); got=1;}
    if(s4 & 0x04){Serial.print("ipkvalid? 04h.D2 st3=0x");Serial.print(s3,HEX);Serial.print(" st4=0x");Serial.println(s4,HEX); got=1;}
    if(!got){delay(50);}
  }

  if(!got){
    Serial.println("TIMEOUT no packet (try STATE:<hex> to set rxon bit)");
    Serial.print(" final 03h=0x"); Serial.print(rd(0x03),HEX);
    Serial.print(" 04h=0x"); Serial.println(rd(0x04),HEX);
    idle(); return;
  }

  // Read RX FIFO (burst read, one CS assertion)
  delay(10); // settle
  digitalWrite(CS,0); delayMicroseconds(20);
  xf(0x7F); // FIFO burst-read address (no 0x80 bit)
  Serial.print("RX[");
  for(uint8_t i=0;i<64;i++){
    uint8_t b=xf(0);
    if(b==0 && i>0){break;} // end of data (FIFO returns 0 past data)
    if(i>0) Serial.print(" ");
    if(b>=0x20 && b<=0x7E){Serial.write(b);}
    Serial.print("[0x"); if(b<0x10)Serial.print("0");Serial.print(b,HEX);Serial.print("]");
  }
  Serial.println("]");

  // Clear interrupts
  rd(0x03); rd(0x04);

  idle();
  Serial.println("RX done");
}

// --- Idle ---
void idle(){
  wr(0x07,0x01); // READY (xton=1)
  digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
}
