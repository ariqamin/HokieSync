# IMPLEMENTATION

## Overview
For this milestone, a working prototype of the academic planning Discord bot described in the proposal and design documents was implemented. The main goal of this version was not to finish every possible feature, but to build a vertical slice that proves the system works end to end inside Discord. AI was used to assist with the drafting of the Implementation.md itself, however, all group members checked and updated the md file as necessary to ensure correctness and readability. 

In its current form, the bot supports the main user flows planned around schedule management, free-time matching, privacy controls, recommendations, and class watch notifications. It is meant to demonstrate the core system during a final demo while still leaving room for future work like real API integrations and fuller DARS support.

## What was implemented
The current implementation includes these features:

- user profile creation and updates
- adding a class by CRN
- editing and removing saved classes
- viewing a personal schedule
- viewing another user’s schedule when privacy rules allow it
- privacy settings with `public`, `friends`, and `private`
- a friends list for schedule sharing
- shared free-time calculation across multiple users
- a recommendation flow with multiple recommendation modes
- a class watchlist with seat alert polling
- admin commands for status, configuration, and demo seat simulation

This version is designed to be demo-friendly. It works with mock course data so the full bot can still run even without live Virginia Tech integrations.

## Main files and what they do

### `bot.py`
This is the main entry point for the bot and the file where most of the interaction logic lives. It sets up the Discord bot, loads the settings, creates the shared services, and registers the slash commands.

This file handles the command-level behavior for:

- `/profile`
- `/addclass`
- `/editclass`
- `/removeclass`
- `/myschedule`
- `/schedule`
- `/privacy`
- `/addfriend`
- `/removefriend`
- `/free`
- `/watchclass`
- `/unwatchclass`
- `/config`
- `/status`
- `/simulateseats`
- `/help`

It also contains the polling loop that checks watched classes and sends seat alerts when a monitored class opens up.

A large portion of the implementation effort went into this file because it connects together the rest of the system. It acts as the controller layer for the project.

### `config.py`
This file is responsible for loading environment-based settings for the bot. It defines the `Settings` dataclass and the `load_settings()` function. In practice, this is where the bot reads values such as the Discord token, guild ID, database path, and polling interval. The file keeps setup values in one place so the rest of the bot does not have hardcoded configuration scattered throughout the codebase.

This file is simple, but it is important because it makes the project easier to run, test, and move between machines.

### `models.py`
This file defines the shared data structures used across the bot. It includes the core dataclasses and constants that represent the system’s data.

The main pieces in this file are:

- `Profile`
- `ClassEntry`
- `Recommendation`
- valid privacy settings
- valid recommendation modes
- weekday constants

This file helps keep the rest of the code cleaner because different services can all rely on the same structure for profiles, classes, and recommendation results.

## AI usage summary
AI was used as a development aid during implementation, mainly to speed up the first draft of the bot structure and some of the command logic. ChatGPT was used to help generate and organize parts of the working prototype, especially the larger controller file where the slash commands are defined.

AI was most helpful for:

- scaffolding the Discord bot structure
- generating command handler boilerplate
- organizing repeated response formatting patterns
- drafting the watchlist polling loop
- helping structure the prototype around services and data models

AI was **not** used as a replacement for reviewing the code. After generation, parts of the file were rewritten to make the code more readable and more consistent with how a student developer would normally write it.

## AI-generated portions of the project
The parts of the project that were most directly AI-assisted were:

- the initial draft of `bot.py`
- parts of the command registration structure
- some of the repeated Discord response blocks
- some of the early setup for configuration and models

The final version was then revised manually to better match the project requirements and to make the code look cleaner and more human-written.

## Tool used
The main AI tool used for this work was ChatGPT.

It was used for code generation, restructuring, and cleanup. It was also used to help align the implementation with the requirements and deliverables from the project PDFs.

## What AI was expected to generate
The goal in using AI was not to have it magically produce a finished project with no editing. The expectation was:

- a solid starting structure for a Discord bot
- slash command handlers that matched the project requirements
- working connections between the bot layer and the service layer
- a prototype that could actually be run and demonstrated

In other words, AI was expected to help get past the blank-page stage and generate a functional first version that could then be refined.

## What AI actually generated
AI helped produce a working version of the main bot file and basic supporting structure. It was able to generate the major slash commands, connect them to services, and keep the feature set close to the proposal.

The generated code was useful, but some of it read too much like generated code. A lot of the logic was packed into one-liners or written in a style that felt less natural than code a student would normally turn in. Because of that, the AI output was treated as a draft rather than a final submission.

## Modifications made after generation
After the initial generation, several changes were made so the code felt more readable and more like something written by an actual student developer.

The main edits were:

- breaking apart compressed one-line expressions into normal multi-line blocks
- removing unnecessary return type arrows like `-> None` from command functions
- rewriting ternary expressions into plain `if` statements when that improved readability
- cleaning up import formatting and long function calls
- removing AI-related comments from the code itself
- keeping the logic the same while making the style more natural

So even though AI helped with the first draft, manual cleanup was still needed to make the final code easier to read and defend during a demo.

## Why AI was useful here
AI was useful because the project has a lot of repeated interaction logic and setup code. For a Discord bot, there is a lot of boilerplate around slash commands, interaction responses, and task loops. AI helped speed up that repetitive setup and made it easier to focus on whether the commands matched the project requirements.

It was especially helpful for quickly generating a working prototype that could be tested and revised.

## Limits of the current implementation
Even though the prototype works, it is still not the fully completed production version of the project.

What is still simplified or mocked:

- real Virginia Tech course API integration
- real RateMyProfessor integration
- real grade-history integration
- full DARS upload and parsing
- stronger recommendation quality based on real academic data

So the current implementation is best understood as a working prototype that demonstrates the architecture and the main flows, not as the final polished release.

## Final reflection
Overall, AI helped move the implementation forward faster, especially for the first draft of the bot structure and command logic. The most important part, though, was still reviewing, rewriting, and understanding the generated code. The AI output was not treated as automatically correct or final.

The final result is a bot that can be demonstrated live, shows multiple working functions, and matches the project direction well enough for this stage of development.
