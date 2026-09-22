#define CS 10
#define SCK 11
#define MOSI 13
#define MISO 12
#define SDN 9
#define SDN2 8
#define RXON 3
#define TXON 4
#define IRQ 2

uint8_t xf(uint8_t o){uint8_t in=0; for(int i=7;i>=0;i--){digitalWrite(MOSI,(o>>i)&1);delayMicroseconds(50);digitalWrite(SCK,1);delayMicroseconds(50);in|=(digitalRead(MISO)&1)<<i;digitalWrite(SCK,0);delayMicroseconds(50);} return in;}
uint8_t rd(uint8_t a){digitalWrite(CS,0);delayMicroseconds(20);xf(a&0x7F);uint8_t v=xf(0);digitalWrite(CS,1);delayMicroseconds(20);return v;}
void wr(uint8_t a,uint8_t v){digitalWrite(CS,0);delayMicroseconds(20);xf(0x80|a);xf(v);digitalWrite(CS,1);delayMicroseconds(20);}

struct Reg {uint8_t a; uint8_t v;};
Reg cfg[] = {
  {0x1C,0x81},{0x1D,0x40},{0x20,0x64},{0x21,0x01},{0x22,0x47},{0x23,0xAE},{0x24,0x06},{0x25,0x27},
  {0x2A,0x50},{0x30,0xAC},{0x32,0x8C},{0x33,0x0A},{0x34,0x08},{0x35,0x2A},{0x36,0x2D},{0x37,0xD4},
  {0x3E,0x07}, // payload len 7 for GALAMAD
  {0x6E,0x4E},{0x6F,0xA4}, // 9600 bps scale1
  {0x70,0x2C}, // txdtrtscale=1
  {0x71,0x23}, // GFSK FIFO
  {0x72,0x08}, // dev ~5k
  {0x75,0x53},{0x76,0x64},{0x77,0x00}, // 434.0 MHz (fb=19 fc=0x6400)
  {0x6D,0x18}, // TX power default
  {0x08,0x00}, // Operating Control 2
};

void setup(){
 Serial.begin(115200); delay(2000);
 pinMode(CS,OUTPUT); digitalWrite(CS,1);
 pinMode(SCK,OUTPUT); digitalWrite(SCK,0);
 pinMode(MOSI,OUTPUT); digitalWrite(MOSI,0);
 pinMode(MISO,INPUT);
 pinMode(SDN,OUTPUT); pinMode(SDN2,OUTPUT);
 pinMode(RXON,OUTPUT); pinMode(TXON,OUTPUT);
 pinMode(IRQ,INPUT_PULLUP);
 digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
 Serial.println("=== SST01 TX TEST 434MHz GFSK 9600 ===");
 digitalWrite(SDN,HIGH); digitalWrite(SDN2,HIGH); delay(500);
 digitalWrite(SDN,LOW); digitalWrite(SDN2,LOW); delay(500);
 Serial.println("POR done");
 // SWRESET
 wr(0x07,0x80); delay(300); rd(0x03); rd(0x04);
 Serial.println("SWRST done");
 Serial.print("DT="); Serial.println(rd(0x00),HEX);
 // program regs
 for(auto r:cfg){ wr(r.a,r.v); delay(5); }
 Serial.println("Regs programmed");
 for(auto r:cfg){ uint8_t v=rd(r.a); Serial.print("0x"); if(r.a<16) Serial.print("0"); Serial.print(r.a,HEX); Serial.print("=0x"); if(v<16) Serial.print("0"); Serial.print(v,HEX); if(v!=r.v) Serial.print("(!)"); Serial.print(" "); }
 Serial.println();
 // set idle mode TXON=H RXON=H
 digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
 delay(100);
}

uint8_t payload[] = {'G','A','L','A','M','A','D'};
int pkt=0;
void loop(){
 pkt++;
 // FULL POR re-arm: SST01 requires a power-cycle (SDN shutdown toggle) between
 // FIFO/PH packet transmissions. SWRESET then RXON=H TXON=H before refill.
 digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
 digitalWrite(SDN,HIGH); digitalWrite(SDN2,HIGH); delay(150);
 digitalWrite(SDN,LOW); digitalWrite(SDN2,LOW); delay(200);
 wr(0x07,0x80); delay(200); rd(0x03); rd(0x04);
 for(auto r:cfg){ wr(r.a,r.v); delay(3); }
 digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
 // clear TX FIFO
 wr(0x08,0x01); delay(5); wr(0x08,0x00); delay(5);
 // fill FIFO
 digitalWrite(CS,0); delayMicroseconds(20);
 xf(0x80|0x7F);
 for(int i=0;i<7;i++) xf(payload[i]);
 digitalWrite(CS,1); delayMicroseconds(20);
 Serial.print("PKT "); Serial.print(pkt); Serial.println(" FIFO loaded GALAMAD");
 // TXON low RXON high = TX mode (RF switch)
 digitalWrite(RXON,HIGH); digitalWrite(TXON,LOW);
 // enter TX state
 wr(0x07,0x09); // txon=1
 Serial.println("TXON...");
 // poll ipksent
 unsigned long t0=millis();
 while(millis()-t0 < 150){
   uint8_t st=rd(0x03);
   if(st & 0x04){ // ipksent bit2? datasheet 03: ipksent = D2?
     Serial.print("ipksent st=0x"); Serial.println(st,HEX);
     rd(0x03); rd(0x04); // clear
     break;
   }
   delay(10);
 }
 // back to idle
 wr(0x07,0x01); // ready
 digitalWrite(RXON,HIGH); digitalWrite(TXON,HIGH);
 Serial.print("Done pkt "); Serial.println(pkt);
 delay(30);
}
