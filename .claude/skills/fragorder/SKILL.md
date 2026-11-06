---
name: fragorder
description: Ask the frag-order builder to create a frag order (unit, automat or crowd; with or without a mission) in sw.models on the current branch, wired through FragOrder.lua and classified for checkpoint survival (writes the files, never commits). Usage /fragorder <what the order does, receivers, missions, parameters, how it ends>
argument-hint: <frag order to build>
disable-model-invocation: true
context: fork
agent: sw-fragorder-builder
---

$ARGUMENTS
