from taskbot.entrypoints.worker.launcher import launch

if __name__ == "__main__":
    import sys

    launch(sys.argv[1:])
