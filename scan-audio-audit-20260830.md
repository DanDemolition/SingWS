# Scan audio audit — 2026-08-30

Read-only audit of 11 retryable tracks logged after the scan restart. All ZIPs passed CRC testing and contained one MP3. Independent FFmpeg decoding found damaged audio in every track. No library files or caches changed.

| Archive | Independent decode result |
| --- | --- |
| MM6045 04 - Rod Stewart - Having A Party.zip | 1153 packet decoding errors |
| MM6044 05 - Lorrie Morgan - My Night To Howl.zip | 1015 packet decoding errors |
| HV08 02 - Coolio - Gangsta's Paradise.zip | 1435 packet decoding errors |
| MM6042 13 - Wings - Maybe I'm Amazed.zip | 790 packet decoding errors |
| MM6049 13 - Genesis - Hold On To My Heart.zip | 1048 packet decoding errors |
| LG221 15 - Michael Jackson - I Want You Back.zip | 653 packet decoding errors |
| MM6058 15 - Queen - We Will Rock You.zip | 735 packet decoding errors |
| MM6044 14 - Linda Davis - Company Time.zip | 235 packet decoding errors |
| MM6042 14 - Meatloaf - You Took The Words Right Out Of My Mouth.zip | 2797 packet decoding errors |
| TU202 15 - Tatu - Show Me Love.zip | 1603 packet decoding errors |
| SF348 10 - James Bay - Let It Go.zip | Cannot open: no two consecutive MPEG audio frames |

Re-encoding cannot reliably restore missing audio or guarantee CDG timing. Recommended: quarantine these exact archive versions and replace from intact originals. This is not a full-library health certificate; the scan is still running.
