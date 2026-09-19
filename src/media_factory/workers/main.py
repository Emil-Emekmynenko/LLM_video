from redis import Redis
from rq import Queue, Worker

from media_factory.config import get_settings


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.queue_name, connection=connection)
    Worker([queue], connection=connection).work()


if __name__ == "__main__":
    main()

