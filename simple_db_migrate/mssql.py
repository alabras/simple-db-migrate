import pyodbc 
from .core import Migration
from .core.exceptions import MigrationException
from .helpers import Utils

class MSSQL(object):

    def __init__(self, config=None, mssql_driver=None):
        self.__mssql_script_encoding = config.get("database_script_encoding", "utf8")
        self.__mssql_encoding = config.get("database_encoding", "utf8")
        self.__mssql_host = config.get("database_host")
        self.__mssql_port = config.get("database_port", "1433")
        self.__mssql_user = config.get("database_user")
        self.__mssql_passwd = config.get("database_password")
        self.__mssql_db = config.get("database_name")
        self.__version_table = config.get("database_version_table")

        self.__mssql_driver = mssql_driver
        if not mssql_driver:
            self.__mssql_driver = pyodbc

        if config.get("drop_db_first"):
            self._drop_database()

        self._create_database_if_not_exists()
        self._create_version_table_if_not_exists()

    def __mssql_connect(self, connect_using_database_name=True):
        try:
            if connect_using_database_name:
                conn = pyodbc.connect('DRIVER={ODBC Driver 17 for SQL Server};SERVER='+self.__mssql_host+';PORT=1433;DATABASE='+self.__mssql_db+';UID='+self.__mssql_user+';PWD='+ self.__mssql_passwd)
            else:
                conn = pyodbc.connect('DRIVER={ODBC Driver 17 for SQL Server};SERVER='+self.__mssql_host+';PORT=1433;DATABASE=master;UID='+self.__mssql_user+';PWD='+ self.__mssql_passwd)
            return conn
        except Exception as e:
            raise Exception("could not connect to database: %s" % e)

    def __execute(self, sql, execution_log=None):
        db = self.__mssql_connect()
        cursor = db.cursor()
        db.autocommit = False
        curr_statement = None
        try:
            statments = MSSQL._parse_sql_statements(sql)
            if len(sql.strip(' \t\n\r')) != 0 and len(statments) == 0:
                raise Exception("invalid sql syntax '%s'" % Utils.encode(sql, "utf-8"))
            
            for statement in statments:
                curr_statement = statement
                affected_rows = cursor.execute(statement).rowcount
                cursor.commit()
                if execution_log:
                    execution_log("%s\n-- %d row(s) affected\n" % (statement, affected_rows and int(affected_rows) or 0))
        except Exception as e:
            raise MigrationException("error executing migration: %s" % e, curr_statement)
        finally:
            cursor.close()
            db.close()

    @classmethod
    def _parse_sql_statements(cls, migration_sql):
        all_statements = []
        last_statement = ''

        for statement in migration_sql.split(';'):
            if len(last_statement) > 0:
                curr_statement = '%s;%s' % (last_statement, statement)
            else:
                curr_statement = statement

            count = Utils.count_occurrences(curr_statement)
            single_quotes = count.get("'", 0)
            double_quotes = count.get('"', 0)
            left_parenthesis = count.get('(', 0)
            right_parenthesis = count.get(')', 0)

            if single_quotes % 2 == 0 and double_quotes % 2 == 0 and left_parenthesis == right_parenthesis:
                all_statements.append(curr_statement)
                last_statement = ''
            else:
                last_statement = curr_statement

        return [s.strip() for s in all_statements if ((s.strip() != "") and (last_statement == ""))]

    def _drop_database(self):
        db = self.__mssql_connect(False)
        cursor = db.cursor()
        try:
            cursor.execute("if exists ( select 1 from sysdatabases where name = '%s' ) drop database %s;" % (self.__mssql_db, self.__mssql_db))
            cursor.commit()
        except Exception as e:
            raise Exception("can't drop database '%s'; \n%s" % (self.__mssql_db, str(e)))
        finally:
            cursor.close()
            db.close()

    def _create_database_if_not_exists(self):
        db = self.__mssql_connect(False)
        cursor = db.cursor()
        cursor.execute("if not exists ( select 1 from sysdatabases where name = '%s' ) create database %s;" % (self.__mssql_db, self.__mssql_db))
        cursor.commit()
        cursor.close()
        db.close()

    def _create_version_table_if_not_exists(self):
        # create version table
        sql = "if not exists ( select 1 from sysobjects where name = '%s' and type = 'u' ) create table %s ( id INT IDENTITY(1,1) NOT NULL PRIMARY KEY, version varchar(20) NOT NULL default '0', label varchar(255), name varchar(255), sql_up NTEXT, sql_down NTEXT);" % (self.__version_table, self.__version_table)
        self.__execute(sql)

        # check if there is a register there
        db = self.__mssql_connect()
        cursor = db.cursor()
        count = db.execute("select count(*) from %s;" % self.__version_table).fetchone()
        cursor.close()
        db.close()

        # if there is not a version register, insert one
        if count == 0:
            sql = "insert into %s (version) values ('0');" % self.__version_table
            self.__execute(sql)

    def __change_db_version(self, version, migration_file_name, sql_up, sql_down, up=True, execution_log=None, label_version=None):
        params = []
        params.append(version)

        if up:
            # moving up and storing history
            sql = "insert into %s (version, label, name, sql_up, sql_down) values (?, ?, ?, ?, ?);" % (self.__version_table)
            params.append(label_version)
            params.append(migration_file_name)
            params.append(sql_up and Utils.encode(sql_up, self.__mssql_script_encoding) or "")
            params.append(sql_down and Utils.encode(sql_down, self.__mssql_script_encoding) or "")
        else:
            # moving down and deleting from history
            sql = "delete from %s where version = ?;" % (self.__version_table)

        db = self.__mssql_connect()
        cursor = db.cursor()
        try:
            cursor.execute(Utils.encode(sql, self.__mssql_script_encoding), tuple(params))
            cursor.commit()
            if execution_log:
                execution_log("migration %s registered\n" % (migration_file_name))
        except Exception as e:
            raise MigrationException("error logging migration: %s" % e, migration_file_name)
        finally:
            cursor.close()
            db.close()

    def change(self, sql, new_db_version, migration_file_name, sql_up, sql_down, up=True, execution_log=None, label_version=None):
        self.__execute(sql, execution_log)
        self.__change_db_version(new_db_version, migration_file_name, sql_up, sql_down, up, execution_log, label_version)

    def get_current_schema_version(self):
        db = self.__mssql_connect()
        cursor = db.cursor()
        version = cursor.execute("select top 1 version from %s order by id desc" % self.__version_table).fetchone()
        cursor.close()
        db.close()
        if version == None:
            return 0
        else:
            return version.version

    def get_all_schema_versions(self):
        versions = []
        db = self.__mssql_connect()
        cursor = db.cursor()
        all_versions = cursor.execute("select version from %s order by id;" % self.__version_table).fetchall()
        for version in all_versions:
            versions.append(version.version)
        cursor.close()
        db.close()
        versions.sort()
        return versions

    def get_version_id_from_version_number(self, version):
        db = self.__mssql_connect()
        cursor = db.cursor()
        result = cursor.execute("select id from %s where version = '%s' order by id desc;" % (self.__version_table, version)).fetchone()
        _id = result and int(result.id) or None
        cursor.close()
        db.close()
        return _id

    def get_version_number_from_label(self, label):
        db = self.__mssql_connect()
        cursor = db.cursor()
        result = cursor.execute("select version from %s where label = '%s' order by id desc" % (self.__version_table, label)).fetchone()
        version = result and result.version or None
        cursor.close()
        db.close()
        return version

    def get_all_schema_migrations(self):
        migrations = []
        db = self.__mssql_connect()
        cursor = db.cursor()
        all_migrations = cursor.execute("select id, version, label, name, cast(sql_up as text) as sql_up, cast(sql_down as text) as sql_down from %s order by id;" % self.__version_table).fetchall()
        for migration_db in all_migrations:
            migration = Migration(id = migration_db.id,
                                  version = migration_db.version or None,
                                  label = migration_db.label or None,
                                  file_name = migration_db.name or None,
                                  sql_up = Migration.ensure_sql_unicode(migration_db.sql_up, self.__mssql_script_encoding),
                                  sql_down = Migration.ensure_sql_unicode(migration_db.sql_down, self.__mssql_script_encoding))
            migrations.append(migration)
        cursor.close()
        db.close()
        return migrations
