#define CS_PIN 10
#define SCK_PIN 13
#define MOSI_PIN 11
#define MISO_PIN 12
#define SDN_PIN 9
#define SDN_PIN2 8
#define RXON_PIN 3
#define TXON_PIN 4
#define IRQ_PIN 2

uint8_t bb_xfer(uint8_t o){
  uint8_t in=0;
  for(int i=7;i>=0;i--){
    digitalWrite(MOSI_PIN,(o>>i)&1);
    delayMicroseconds(50);
    digitalWrite(SCK_PIN, HIGH);
    delayMicroseconds(50);
    in|= (digitalRead(MISO_PIN)&1)<<i;
    digitalWrite(SCK_PIN, LOW);
    delayMicroseconds(50);
  }
  return in;
}
uint8_t sr(uint8_t a){ digitalWrite(CS_PIN,LOW); delayMicroseconds(10); bb_xfer(a&0x7F); uint8_t v=bb_xfer(0); digitalWrite(CS_PIN,HIGH); delayMicroseconds(10); return v; }
void sw(uint8_t a,uint8_t v){ digitalWrite(CS_PIN,LOW); delayMicroseconds(10); bb_xfer(0x80|a); bb_xfer(v); digitalWrite(CS_PIN,HIGH); delayMicroseconds(10); }

void cfg(){
  sw(0x07,0x80); delay(300);
  sr(0x03); sr(0x04);
  uint32_t hz=434000000UL;
  uint64_t fc=((uint64_t)(hz%10000000UL)*64000UL)/10000000UL;
  uint32_t fb=(hz/10000000UL)-24;
  uint8_t bs=sr(0x75); bs=(bs&0xC0)|(fb&0x1F)|0x40;
  sw(0x75,bs); sw(0x76,(fc>>8)&0xFF); sw(0x77,fc&0xFF);
  sw(0x6E,0x4E); sw(0x6F,0xA4);
  sw(0x71,0x23); sw(0x72,0x08);
  sw(0x6D,0x1F);
  sw(0x30,0x8D); sw(0x32,0x0C); sw(0x33,0x22);
  sw(0x34,0x08); sw(0x35,0x2A); sw(0x36,0x2D); sw(0x37,0xD4);
  sw(0x05,0x04); sw(0x06,0x00);
  sr(0x03); sr(0x04);
}

void setup(){
  Serial.begin(115200); delay(2000);
  Serial.println("\n=== TX GALAMAD ===");
  pinMode(CS_PIN, OUTPUT); digitalWrite(CS_PIN, HIGH);
  pinMode(SCK_PIN, OUTPUT); digitalWrite(SCK_PIN, LOW);
  pinMode(MOSI_PIN, OUTPUT); digitalWrite(MOSI_PIN, LOW);
  pinMode(MISO_PIN, INPUT);
  pinMode(SDN_PIN, OUTPUT); pinMode(SDN_PIN2, OUTPUT);
  pinMode(RXON_PIN, OUTPUT); digitalWrite(RXON_PIN, HIGH);
  pinMode(TXON_PIN, OUTPUT); digitalWrite(TXON_PIN, HIGH);
  pinMode(IRQ_PIN, INPUT_PULLUP);

  digitalWrite(SDN_PIN, HIGH); digitalWrite(SDN_PIN2, HIGH); delay(500);
  digitalWrite(SDN_PIN, LOW); digitalWrite(SDN_PIN2, LOW); delay(500);

  cfg();
  Serial.print("DT="); Serial.println(sr(0x00),HEX);
  Serial.print("30="); Serial.println(sr(0x30),HEX);
  Serial.print("6D="); Serial.println(sr(0x6D),HEX);
  Serial.print("75="); Serial.println(sr(0x75),HEX);
}

const char MSG[] = "GALAMAD";
uint8_t idx=0;

void loop(){
  cfg();
  digitalWrite(RXON_PIN, HIGH); digitalWrite(TXON_PIN, LOW); delay(5);
  sr(0x03); sr(0x04);

  uint8_t pkt[9]; pkt[0]=6;
  for(int i=0;i<6;i++) pkt[i+1]=MSG[i];
  uint16_t crc=0xFFFF;
  for(int i=0;i<7;i++){
    crc^=(uint16_t)pkt[i]<<8;
    for(int b=0;b<8;b++) crc=(crc&0x8000)?(crc<<1)^0x1021:(crc<<1);
  }
  pkt[7]=crc>>8; pkt[8]=crc&0xFF;

  for(int i=0;i<=8;i++) sw(0x7F,pkt[i]);
  sw(0x07,0x09);
  unsigned long t=millis(); bool ok=false;
  while(millis()-t<2500){
    if(digitalRead(IRQ_PIN)==LOW){ sr(0x03); sr(0x04); ok=true; break; }
    delay(1);
  }
  Serial.print("TX #"); Serial.print(++idx);
  Serial.print(" GALAMAD -> ");
  if(ok){ uint8_t st=sr(0x03); Serial.print("PKT sent 0x"); Serial.println(st,HEX); }
  else{ uint8_t st=sr(0x02),i1=sr(0x03),i2=sr(0x04);
    Serial.print("TO st0x"); Serial.print(st,HEX);
    Serial.print(" i1 0x"); Serial.print(i1,HEX);
    Serial.print(" i2 0x"); Serial.println(i2,HEX);
  }

  digitalWrite(SDN_PIN, HIGH); digitalWrite(SDN_PIN2, HIGH);
  delay(20);
  digitalWrite(SDN_PIN, LOW); digitalWrite(SDN_PIN2, LOW);
  delay(500);
}
