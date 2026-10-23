# AI Task Bot

A task-management application. Users create tasks by sending text or voice messages to a Telegram bot. A background worker transcribes voice messages asynchronously through Redis queues. PostgreSQL stores tasks and users. A responsive web dashboard shows the tasks in real time and lets users move each task between Pending, In Progress and Completed. Docker Compose runs the web application, PostgreSQL, Redis and the worker.

Status: in design. Setup, run and verification instructions are added as each part is implemented.
