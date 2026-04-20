from workerbot.app import build_app


def main() -> None:
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
