# SRT-Subtitle-Synchronizer

Linearly stretches/shifts all subtitle timestamps so that:
- the first subtitle's start time maps to a target start time
- the last subtitle's end time maps to a target end time

Supported input formats:
- *.srt – SubRip
- *.txt – MPL2 (times in deciseconds; '/' = italic, '|' = line break)

Input file: 
- original_name.lang.srt / .txt

Output file: 
- original_name_.lang.srt (underscore before the language tag, always written as UTF-8 SRT)
