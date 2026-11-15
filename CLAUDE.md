# Working agreements for this repository

Standing instructions from the project owner. Follow them in every session without being asked again.

1. **Merge when verified.** After a change is tested and its pull request is open, merge the pull request into the
   default branch (`claude/aurum-frontend-foundation`). Both Vercel projects (`aurum-ai`, `aurum-api`) deploy production
   from that branch, so confirm the deployments come up healthy after each merge.
2. **Update every affected documentation section.** With each change, bring all related docs up to date in the same
   pull request: the module document (`AURUM_AI/docs/MODULE_08_WF08_xx.md`), the "Built workflows" table and the "What is
   deliberately NOT built" section in `AURUM_AI/docs/MODULE_08_README.md`, install steps, configuration tables and test
   lists. No section may still describe the previous state.
3. **Save every n8n workflow in the repository.** Each workflow that runs in n8n has its importable JSON in
   `AURUM_AI/n8n/workflows/`, with the API address and tokens replaced by placeholders
   (`REPLACE_WITH_AURUM_SERVICE_TOKEN`, `REPLACE_WITH_AURUM_WEBHOOK_TOKEN`). Add it to
   `AURUM_AI/tests/orchestration/test_workflow_files.py`. Never commit a real token, password or `.env` file.

## Safety rules that always apply

- AURUM never places, approves or executes a trade from automation. n8n only sequences calls; the backend modules decide.
- n8n must never connect to MT5. Module 01 is read-only and demo accounts only.
- Module 06 makes risk decisions; Module 07 records human approvals. Workflows report those decisions unchanged.
