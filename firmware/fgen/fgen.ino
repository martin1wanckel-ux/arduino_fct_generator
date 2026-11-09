// Primitive function generator for the Arduino Uno (ATmega328P).
//
// Outputs a square wave on pin 9 (OC1A) using Timer1 in Fast PWM mode 14
// (TOP = ICR1), so the waveform is generated entirely in hardware.
//
// Serial protocol (115200 baud, newline-terminated commands):
//   F<hz>    set frequency in Hz (float, e.g. F1000 or F0.5)
//   D<pct>   set duty cycle in percent (float, 0..100)
//   E<0|1>   disable / enable the output
//   ?        report status
// Every command is answered with one status line:
//   OK F=<actual hz> D=<actual pct> P=<prescaler> T=<top> E=<0|1>
// or "ERR <message>". On boot the board prints "READY" followed by a status.

const uint8_t OUTPUT_PIN = 9;  // OC1A on the Uno

const uint16_t PRESCALERS[] = {1, 8, 64, 256, 1024};
const uint8_t CLOCK_SELECT[] = {
    _BV(CS10),
    _BV(CS11),
    _BV(CS11) | _BV(CS10),
    _BV(CS12),
    _BV(CS12) | _BV(CS10),
};
const uint8_t NUM_PRESCALERS = sizeof(PRESCALERS) / sizeof(PRESCALERS[0]);

float requestedFreq = 1000.0;
float requestedDuty = 50.0;
bool enabled = true;

// Values actually programmed into the timer, used for status reports.
uint8_t prescalerIndex = 0;
uint16_t top = 0;
uint32_t highCounts = 0;

char lineBuf[32];
uint8_t lineLen = 0;

void applySettings() {
  // Pick the smallest prescaler that fits in 16 bits: that gives the finest
  // frequency and duty-cycle resolution.
  prescalerIndex = NUM_PRESCALERS - 1;
  uint32_t counts = 65536;
  for (uint8_t i = 0; i < NUM_PRESCALERS; i++) {
    float c = (float)F_CPU / ((float)PRESCALERS[i] * requestedFreq);
    if (c <= 65536.0) {
      prescalerIndex = i;
      counts = (uint32_t)(c + 0.5);
      break;
    }
  }
  if (counts < 2) counts = 2;  // 8 MHz is the hard ceiling

  top = counts - 1;
  highCounts = (uint32_t)(requestedDuty / 100.0 * counts + 0.5);
  if (highCounts > counts) highCounts = counts;

  // Stop the timer while reconfiguring so it can never run past a new, lower
  // TOP (which would make it count all the way to 65535 once).
  TCCR1B = 0;
  TCCR1A = 0;
  TCNT1 = 0;
  ICR1 = top;

  if (!enabled || highCounts == 0) {
    digitalWrite(OUTPUT_PIN, LOW);  // pin disconnected from timer: static low
  } else if (highCounts == counts) {
    digitalWrite(OUTPUT_PIN, HIGH);  // 100 % duty: static high
  } else {
    // Non-inverting: pin is high for OCR1A + 1 counts out of TOP + 1.
    OCR1A = highCounts - 1;
    TCCR1A = _BV(COM1A1) | _BV(WGM11);
  }
  TCCR1B = _BV(WGM13) | _BV(WGM12) | CLOCK_SELECT[prescalerIndex];
}

void printStatus() {
  uint32_t counts = (uint32_t)top + 1;
  float actualFreq = (float)F_CPU / ((float)PRESCALERS[prescalerIndex] * counts);
  float actualDuty = 100.0 * highCounts / counts;
  Serial.print(F("OK F="));
  Serial.print(actualFreq, 4);
  Serial.print(F(" D="));
  Serial.print(actualDuty, 3);
  Serial.print(F(" P="));
  Serial.print(PRESCALERS[prescalerIndex]);
  Serial.print(F(" T="));
  Serial.print(top);
  Serial.print(F(" E="));
  Serial.println(enabled ? 1 : 0);
}

void handleCommand(char *cmd) {
  char op = cmd[0];
  char *arg = cmd + 1;

  switch (op) {
    case 'F':
    case 'f': {
      float f = atof(arg);
      if (f <= 0.0) {
        Serial.println(F("ERR frequency must be > 0"));
        return;
      }
      requestedFreq = f;
      break;
    }
    case 'D':
    case 'd': {
      float d = atof(arg);
      if (d < 0.0 || d > 100.0) {
        Serial.println(F("ERR duty must be 0..100"));
        return;
      }
      requestedDuty = d;
      break;
    }
    case 'E':
    case 'e':
      enabled = atoi(arg) != 0;
      break;
    case '?':
      printStatus();
      return;
    default:
      Serial.println(F("ERR unknown command"));
      return;
  }
  applySettings();
  printStatus();
}

void setup() {
  pinMode(OUTPUT_PIN, OUTPUT);
  Serial.begin(115200);
  applySettings();
  Serial.println(F("READY"));
  printStatus();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      if (lineLen > 0) handleCommand(lineBuf);
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
}
