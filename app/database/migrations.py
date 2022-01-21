import datetime
import io
import os

from asyncpg import Connection


class Migrator:
    """Handles database migrations."""

    def __init__(self, connection: Connection) -> None:
        self._connection: Connection = connection

    @classmethod
    def ensure_migrations_directory(cls) -> None:
        """Ensures that there is a migrations directory and creates one if there isn't.

        Additionally, this also makes sure a .migrations file exists in that directory.
        """
        if os.path.exists('./migrations'):
            if not os.path.isdir('./migrations'):
                os.remove('./migrations')
                os.mkdir('./migrations')

            if not os.path.exists(name := './migrations/.migrations'):
                open(name, 'w').close()
        else:
            os.mkdir('./migrations')
            return cls.ensure_migrations_directory()

    @classmethod
    def create_migration(cls, name: str) -> None:
        """Creates a migration file."""
        cls.ensure_migrations_directory()
        filename = f'./migrations/{name}-{datetime.datetime.utcnow().timestamp():.0f}.migration.sql'

        print('Attempting to create migration file.')
        try:
            open(filename, 'w').close()
        except Exception:
            print('Error while trying to create migration file:')
            raise
        else:
            print(f'Successfully created migration file at {filename}')

    # noinspection PyUnboundLocalVariable
    async def run_migrations(self, *, debug: bool = False) -> None:
        """Runs all migrations.

        Parameters
        ----------
        debug: bool = False
            Whether or not to enable debug logging.
        """

        self.ensure_migrations_directory()
        count = success = 0

        if debug:
            print('Starting migrations...')

        with open('./migrations/.migrations', 'r+') as fp:
            migrated = fp.readlines()
            
            # This ensures we are on a newline
            fp.seek(0, io.SEEK_END)

            for file in os.listdir('./migrations'):
                if file in migrated or not file.endswith('.sql'):
                    continue
                    
                if debug:
                    print(f'Migrating {file}...')
                try:
                    await self._connection.execute(open('./migrations/' + file).read())
                except Exception as exc:
                    print(f'Error when trying to migrate {file}: {exc}')
                else:
                    fp.write(file + '\n')
                    if debug:
                        print(f'Migrated {file}.')

                    success += 1
                finally:
                    count += 1

        if debug:
            print(f'Finished executing {success}/{count} migrations.')
