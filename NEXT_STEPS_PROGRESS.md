# Progress Notes

## Added in this build

- VT timetable provider path through the official VT timetable endpoint
- RateMyProfessors GraphQL provider path
- grade-history provider for CSV, JSON, and request-based loading
- source refresh and source status commands
- Discord upload flow for grade exports
- DARS upload flow for profile hints and in-progress term courses
- recommendation enrichment from live professor and grade data when available
- mock fallback preserved so the bot still runs if a live source is missing
- natural-language schedule planning now parses GPA floors, time windows, avoided days, target course counts, and preferred recommendation modes
- `/planschedule` combines preference parsing and schedule generation into one Discord command

## Still environment-dependent

- live network access from the machine running the bot
- a working Discord bot token in `.env`
- either a UDC export or request details if grade-history data is not publicly reachable from the bot runtime
- optional school ID override for RateMyProfessors if auto lookup fails

## DARS

DARS parsing now reads selectable PDFs or OCRs image-style PDFs. It imports major/term hints, requirement tokens, and in-progress courses for the selected term without saving the PDF contents.
