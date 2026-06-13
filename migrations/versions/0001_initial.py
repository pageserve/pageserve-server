from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

    # Users
    op.execute(
        """
        CREATE TABLE users (
          id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          email           VARCHAR(255) NOT NULL UNIQUE,
          password_hash   VARCHAR(255) NOT NULL,
          full_name       VARCHAR(255),
          role            VARCHAR(20)  NOT NULL DEFAULT 'member'
                          CHECK (role IN ('admin', 'member')),
          is_active       BOOLEAN      NOT NULL DEFAULT true,
          avatar_url      VARCHAR(500),
          created_by      UUID         REFERENCES users(id) ON DELETE SET NULL,
          created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
          last_login_at   TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX idx_users_email ON users(email);")
    op.execute("CREATE INDEX idx_users_role ON users(role) WHERE is_active = true;")

    # Refresh Tokens
    op.execute(
        """
        CREATE TABLE refresh_tokens (
          id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id       UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash    VARCHAR(255) NOT NULL UNIQUE,
          user_agent    TEXT,
          ip_address    VARCHAR(45),
          expires_at    TIMESTAMPTZ  NOT NULL,
          created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
          last_used_at  TIMESTAMPTZ,
          revoked_at    TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX idx_rt_user_id ON refresh_tokens(user_id);")
    op.execute("CREATE INDEX idx_rt_token_hash ON refresh_tokens(token_hash);")

    # Projects
    op.execute(
        """
        CREATE TABLE projects (
          id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          name        VARCHAR(255) NOT NULL,
          slug        VARCHAR(100) NOT NULL UNIQUE,
          description TEXT         NOT NULL DEFAULT '',
          created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
          updated_at  TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX idx_projects_slug ON projects(slug);")

    # Project Members
    op.execute(
        """
        CREATE TABLE project_members (
          user_id     UUID        NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
          project_id  UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          role        VARCHAR(20) NOT NULL DEFAULT 'member'
                      CHECK (role IN ('admin', 'member')),
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, project_id)
        );
        """
    )
    op.execute("CREATE INDEX idx_pm_project ON project_members(project_id);")

    # API Keys
    op.execute(
        """
        CREATE TABLE api_keys (
          id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id     UUID         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          user_id        UUID         NOT NULL REFERENCES users(id),
          name           VARCHAR(255) NOT NULL,
          public_key     VARCHAR(60)  NOT NULL UNIQUE,
          secret_hash    VARCHAR(255) NOT NULL UNIQUE,
          secret_prefix  VARCHAR(20)  NOT NULL,
          key_type       VARCHAR(10)  NOT NULL DEFAULT 'live'
                         CHECK (key_type IN ('live', 'test')),
          scopes         TEXT[]       NOT NULL DEFAULT ARRAY['read', 'write'],
          is_active      BOOLEAN      NOT NULL DEFAULT true,
          last_used_at   TIMESTAMPTZ,
          last_used_ip   VARCHAR(45),
          request_count  BIGINT       NOT NULL DEFAULT 0,
          expires_at     TIMESTAMPTZ,
          created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_keys_project ON api_keys(project_id);")
    op.execute("CREATE INDEX idx_keys_public_key ON api_keys(public_key);")
    op.execute("CREATE INDEX idx_keys_hash ON api_keys(secret_hash);")
    op.execute(
        "CREATE INDEX idx_keys_active ON api_keys(public_key) WHERE is_active = true;"
    )

    # Documents
    op.execute(
        """
        CREATE TABLE documents (
          id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id  UUID         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          name        VARCHAR(500) NOT NULL,
          doc_type    VARCHAR(10)  NOT NULL DEFAULT 'pdf',
          page_count  INTEGER,
          file_size   BIGINT,
          description TEXT         NOT NULL DEFAULT '',
          tags        TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
          language    VARCHAR(10)  NOT NULL DEFAULT 'vi',
          status      VARCHAR(20)  NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'indexing', 'completed', 'failed')),
          error_msg   TEXT,
          created_by  UUID         REFERENCES users(id) ON DELETE SET NULL,
          created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
          updated_at  TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX idx_docs_project ON documents(project_id);")
    op.execute("CREATE INDEX idx_docs_status ON documents(project_id, status);")
    op.execute("CREATE UNIQUE INDEX idx_docs_name ON documents(project_id, name);")

    # Structure (Tree Index)
    op.execute(
        """
        CREATE TABLE structure (
          doc_id     UUID        PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
          tree       JSONB       NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_structure_tree ON structure USING GIN (tree);")

    # Pages
    op.execute(
        """
        CREATE TABLE pages (
          doc_id   UUID    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          page_num INTEGER NOT NULL,
          content  TEXT    NOT NULL DEFAULT '',
          PRIMARY KEY (doc_id, page_num)
        );
        """
    )
    op.execute("CREATE INDEX idx_pages_doc ON pages(doc_id);")

    # Audit Logs
    op.execute(
        """
        CREATE TABLE audit_logs (
          id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID         REFERENCES users(id) ON DELETE SET NULL,
          project_id  UUID         REFERENCES projects(id) ON DELETE SET NULL,
          action      VARCHAR(50)  NOT NULL,
          resource    VARCHAR(255),
          detail      JSONB,
          ip_address  VARCHAR(45),
          created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_audit_project ON audit_logs(project_id, created_at DESC);"
    )
    op.execute("CREATE INDEX idx_audit_user ON audit_logs(user_id, created_at DESC);")
    op.execute("CREATE INDEX idx_audit_action ON audit_logs(action, created_at DESC);")

    # Webhooks
    op.execute(
        """
        CREATE TABLE webhooks (
          id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id  UUID         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          url         VARCHAR(500) NOT NULL,
          secret      VARCHAR(100),
          events      TEXT[]       NOT NULL
                      DEFAULT ARRAY['document.completed', 'document.failed'],
          is_active   BOOLEAN      NOT NULL DEFAULT true,
          created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_webhooks_project ON webhooks(project_id) WHERE is_active = true;"
    )

    # Playground History
    op.execute(
        """
        CREATE TABLE playground_history (
          id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          project_id  UUID         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          doc_ids     UUID[]       NOT NULL DEFAULT ARRAY[]::UUID[],
          question    TEXT         NOT NULL,
          mode        VARCHAR(10)  NOT NULL DEFAULT 'answer'
                      CHECK (mode IN ('answer', 'search')),
          response    JSONB,
          elapsed_ms  INTEGER,
          starred     BOOLEAN      NOT NULL DEFAULT false,
          created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_ph_user ON playground_history(user_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX idx_ph_starred ON playground_history(user_id, starred) WHERE starred = true;"
    )


def downgrade() -> None:
    for table in (
        "playground_history",
        "webhooks",
        "audit_logs",
        "pages",
        "structure",
        "documents",
        "api_keys",
        "project_members",
        "projects",
        "refresh_tokens",
        "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
