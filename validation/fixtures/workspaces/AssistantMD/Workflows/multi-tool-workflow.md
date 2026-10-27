---
schedule: once: 2030-01-01 09:00
workflow_engine: step
enabled: true
description: Multi-tool testing workflow that validates all tool backends
---

## INSTRUCTIONS
You are a testing workflow that validates different tool capabilities. Each step tests a specific tool independently.

## STEP1_WEB_SEARCH
@model haiku
@tools web_search
@run-on monday, tuesday, wednesday, thursday, friday, saturday, sunday
@output file: tools/duckduckgo-test

Use the configured web search strategy to find information about "Python programming basics" and provide a brief summary.

## STEP2_CODE_EXECUTION_GENERIC
@model haiku
@tools code_execution
@run-on monday, tuesday, wednesday, thursday, friday, saturday, sunday
@output file: tools/code-execution-generic-test

Use the generic code execution tool to calculate 7 * 8 and show the result.

## STEP3_WEB_EXTRACT
@model haiku
@tools web_extract
@run-on monday, tuesday, wednesday, thursday, friday, saturday, sunday
@output file: tools/tavily-extract-test

Use the configured web extraction strategy to extract content from https://docs.python.org/3/tutorial/introduction.html and provide a summary of the Python introduction.

## STEP4_WEB_CRAWL
@model haiku
@tools web_crawl
@run-on monday, tuesday, wednesday, thursday, friday, saturday, sunday
@output file: tools/tavily-crawl-test

Use the configured web crawl strategy to crawl content from https://www.python.org and provide a summary of what you find on the Python website.
