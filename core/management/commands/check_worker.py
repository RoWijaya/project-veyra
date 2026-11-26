from django.core.management.base import BaseCommand, CommandError
from redis import RedisError

from core.workers import WORKERS, heartbeat_key, redis_client


class Command(BaseCommand):
    help = "Check recent progress of a supervised worker."

    def add_arguments(self, parser):
        parser.add_argument("worker", choices=WORKERS)
        parser.add_argument("--instance", help="Replica identity. Defaults to this container's identity.")

    def handle(self, *args, **options):
        worker = options["worker"]
        try:
            with redis_client() as redis:
                alive = redis.exists(heartbeat_key(worker, options["instance"]))
        except RedisError:
            alive = False
        if not alive:
            raise CommandError(f"No recent progress from {worker}.")
        self.stdout.write(f"{worker} is making progress.")
