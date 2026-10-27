-- Limite de tentativas de login.
--
-- Cada tentativa é contada antes de a senha ser verificada, numa atualização atômica:
-- requisições paralelas não conseguem passar todas pela checagem ao mesmo tempo.
-- Três chaves por tentativa: conta+IP (limite baixo), conta (vindo de vários IPs) e IP
-- (várias contas). Bloquear só por conta deixaria qualquer um travar a conta de outra pessoa;
-- por isso o limite por conta isolada é mais alto que o de conta+IP.

CREATE TABLE op.limite_login (
  chave         text PRIMARY KEY CHECK (length(chave) <= 120),
  falhas        integer NOT NULL DEFAULT 0 CHECK (falhas >= 0),
  janela_inicio timestamptz NOT NULL DEFAULT now(),
  bloqueado_ate timestamptz
);
CREATE INDEX limite_login_antigos ON op.limite_login (janela_inicio);

CREATE FUNCTION op.tentar_login(p_login text, p_ip text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  janela constant interval := interval '15 minutes';
  bloqueio constant interval := interval '15 minutes';
  v_login text := lower(left(coalesce(p_login, ''), 60));
  v_ip text := left(coalesce(p_ip, 'desconhecido'), 45);
  k record;
  v_espera integer := 0;
  v_falhas integer;
  v_bloq timestamptz;
BEGIN
  -- Limpeza incremental de chaves antigas, sem varrer a tabela inteira.
  DELETE FROM op.limite_login WHERE ctid IN (
    SELECT ctid FROM op.limite_login WHERE janela_inicio < now() - interval '1 day' LIMIT 50);

  FOR k IN SELECT * FROM (VALUES ('conta_ip:' || v_login || '|' || v_ip, 5),
                                 ('conta:' || v_login, 20),
                                 ('ip:' || v_ip, 30)) AS t(chave, limite) LOOP
    INSERT INTO op.limite_login AS l (chave, falhas) VALUES (k.chave, 0)
    ON CONFLICT (chave) DO NOTHING;
    -- A linha fica travada até o fim da transação: tentativas paralelas são serializadas aqui.
    -- Bloqueio vencido ou janela expirada recomeçam a contagem do zero.
    UPDATE op.limite_login AS l SET
        falhas = CASE WHEN l.bloqueado_ate > now() THEN l.falhas
                      WHEN l.bloqueado_ate IS NOT NULL OR l.janela_inicio < now() - janela THEN 1
                      ELSE l.falhas + 1 END,
        janela_inicio = CASE WHEN l.bloqueado_ate > now() THEN l.janela_inicio
                             WHEN l.bloqueado_ate IS NOT NULL OR l.janela_inicio < now() - janela THEN now()
                             ELSE l.janela_inicio END,
        bloqueado_ate = CASE WHEN l.bloqueado_ate > now() THEN l.bloqueado_ate END
     WHERE l.chave = k.chave
    RETURNING l.falhas, l.bloqueado_ate INTO v_falhas, v_bloq;
    IF v_bloq IS NOT NULL AND v_bloq > now() THEN
      v_espera := greatest(v_espera, ceil(extract(epoch FROM v_bloq - now()))::integer);
    ELSIF v_falhas > k.limite THEN
      UPDATE op.limite_login SET bloqueado_ate = now() + bloqueio WHERE chave = k.chave;
      v_espera := greatest(v_espera, extract(epoch FROM bloqueio)::integer);
    END IF;
  END LOOP;
  RETURN v_espera;   -- 0: pode verificar a senha; > 0: segundos até poder tentar de novo
END $$;

-- Login correto: zera conta+IP e devolve a tentativa contada nas outras chaves.
CREATE FUNCTION op.login_bem_sucedido(p_login text, p_ip text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_login text := lower(left(coalesce(p_login, ''), 60));
  v_ip text := left(coalesce(p_ip, 'desconhecido'), 45);
BEGIN
  UPDATE op.limite_login SET falhas = 0, bloqueado_ate = NULL WHERE chave = 'conta_ip:' || v_login || '|' || v_ip;
  UPDATE op.limite_login SET falhas = greatest(falhas - 1, 0)
   WHERE chave IN ('conta:' || v_login, 'ip:' || v_ip) AND (bloqueado_ate IS NULL OR bloqueado_ate <= now());
END $$;

GRANT EXECUTE ON FUNCTION op.tentar_login(text, text), op.login_bem_sucedido(text, text) TO stp_app;
