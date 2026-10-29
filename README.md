# Revenue Desktop Assistant (floating window)
Awaaz sunta hai -> Groq Whisper se text -> sirf revenue/occupancy/Excel/padhai wali baat pakadta hai -> gpt-oss-120b se streaming jawab.

## Setup
    pip install -r requirements.txt
    .env me GROQ_API_KEY daalo
    python main.py --devices     # audio devices ki list
    python main.py

## Audio kaha se
- Bahar ki awaaz (mic): AUDIO_DEVICE khali chhodo
- Computer/YouTube ki awaaz: VB-CABLE install karo, Windows me sound output "CABLE Input" karo, aur
  .env me AUDIO_DEVICE=CABLE Output. (Khud sunne ke liye: Sound settings > Recording > CABLE Output
  > Properties > Listen > "Listen to this device")

## Tuning
- Kuch pakad nahi raha: VAD_THRESHOLD kam karo (0.006). Upar ki ▮ bar awaaz par badhni chahiye.
- Shor se galat trigger: VAD_THRESHOLD badhao.
- Aur fast: SILENCE_SEC=0.35
Window ko kahin se bhi kheench kar hilao, "x" se band.
