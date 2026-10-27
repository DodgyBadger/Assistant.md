---
schedule: cron: 0 10 * * *
workflow_engine: step
enabled: true
description: File operations testing workflow
---

## INSTRUCTIONS

You are a helpful workflow that tests file operations tools. Use lots of emojis! 📁✨

## STEP1
@model sonnet
@tools file_read, file_write
@output file: test-results/step1

Test the file operations capabilities by:
1. Creating a directory called 'files'
2. Writing a test file with some content in that directory
3. Reading the file back to verify it was written correctly
4. List all files in the analysis directory
5. Create a summary of your test results

Use the file_operations tool to perform these tasks and document what you did.

## STEP2
@model sonnet
@tools file_read, file_write
@output file: test-results/step2

Now test the safety features by attempting unsafe operations:
1. Try to overwrite the existing test file (should fail safely)
2. Try to append to a non-existent file (should fail safely)
3. Try to move a file to an existing destination (should fail safely)
4. Document all the safety error messages you receive

Use the file_operations tool to test these safety boundaries and report the results.

## STEP3
@model sonnet
@tools file_read, file_write
@output file: test-results/step3

Test the unsafe file operations tool by performing these operations:

1. Create a template file at 'template.md' with these exact lines:
   # Grant Application
   Project: [INSERT NAME]
   Budget: [INSERT AMOUNT]

2. Use file_write to EDIT_LINE on template.md:
   - Edit line 2, replacing "Project: [INSERT NAME]" with "Project: AI Research"

3. Use file_write to REPLACE_TEXT on template.md:
   - Replace "[INSERT AMOUNT]" with "$50,000"

4. Create a separate file 'delete-test.md' with some content

5. Test DELETE with safety confirmation on delete-test.md:
   - Try delete without matching confirm_path (should fail)
   - Then delete with matching confirm_path (should succeed)

6. Document all operations and their results, including safety validations
