---
name: mission
description: Ask the mission builder to create a checkpoint-resilient unit (agent) mission in sw.models on the current branch (writes the files, never commits). Usage /mission <what the mission does, DBs, unit types, parameters>
argument-hint: <unit mission to build>
disable-model-invocation: true
context: fork
agent: sw-mission-builder
---

$ARGUMENTS
