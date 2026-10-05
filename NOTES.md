# TODO List for features and bugs

1. Regular completion or previously done checkpoints to be referred and not repeat health checks etc on every new session. **Done** Using the context checkpoint at 0.3 in config.yaml as well as LCM plugin now.

2. Refresh session / context even if user continues same chat thread for too long or unrelated tasks. **Done** Done using config.yaml. Not needed now as LCM plugin is doing the smart context mgmt.

3. How to supress working messages and not bother users with loading...executing .py etc...

4. Comprehensive eval suite 

5. Test and design for multiple chat session from same and different users as well as channels.

6. init-bot sequence prioritisation ... right now its not effective enough. **Done** Retest and verify

7. When requested_by user derived from chat channel is not in erp, then there should not be any option to create a user via the agent. Its a hard stop. In fact, we can generalise and have a hard stop on ever creating any user by the bot. **Done**

8. End of day summary grouped by different users who assigned or requested a task execution.

9. End of week performance report auto emailed to mentor/manager.

10. Thoroughly test and ensure all channel chat ids and email message ids persist in Audit Log entry.

11. ERP workflows to enhance checks and approvals in case of bot initiated actions.

12. Deep Agent clarification and research. **Done** Hermes with LCM is good for now.

13. Understanding & Standardising the vocabulary for session, memory and context. **Done**

14. Enable email channel and drive same task from chat as well as email ID.

15. Review and crisp up the scripts (.py) files.


# EVAL Scenarios

## Guardrails - Scope & Functionality

## Accuracy

## Privacy / PII

## Security

