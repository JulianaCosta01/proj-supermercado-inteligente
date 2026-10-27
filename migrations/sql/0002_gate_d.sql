-- 0002: autenticação, correspondência de códigos, imagem ausente e correção da conclusão.

-- ---------------------------------------------------------------- autenticação
ALTER TABLE op.usuario ADD COLUMN login text UNIQUE CHECK (login ~ '^[a-z0-9._-]{3,40}$');

CREATE TABLE op.credencial (
  usuario_id bigint PRIMARY KEY REFERENCES op.usuario ON DELETE CASCADE,
  senha_hash text NOT NULL CHECK (senha_hash LIKE '$argon2id$%')
);

CREATE TABLE op.sessao (
  token_sha256 char(64) PRIMARY KEY,
  usuario_id   bigint NOT NULL REFERENCES op.usuario ON DELETE CASCADE,
  criada_em    timestamptz NOT NULL DEFAULT now(),
  expira_em    timestamptz NOT NULL,
  CHECK (expira_em > criada_em AND expira_em <= criada_em + interval '12 hours')
);
CREATE INDEX sessao_usuario ON op.sessao (usuario_id);

-- Desativar usuário derruba as sessões dele na mesma transação.
CREATE FUNCTION op.usuario_desativado() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.ativo AND NOT NEW.ativo THEN
    DELETE FROM op.sessao WHERE usuario_id = NEW.id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_desativado AFTER UPDATE OF ativo ON op.usuario
  FOR EACH ROW EXECUTE FUNCTION op.usuario_desativado();

-- ---------------------------------------------------------------- códigos do sistema de gestão
CREATE TABLE op.codigo_externo (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id    bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  codigo     text NOT NULL CHECK (length(codigo) BETWEEN 1 AND 40),
  produto_id bigint NOT NULL REFERENCES op.produto ON DELETE CASCADE,
  UNIQUE (loja_id, codigo)
);

-- ---------------------------------------------------------------- imagem ausente (reconciliação)
CREATE TABLE op.imagem_ausente (
  captura_id    bigint PRIMARY KEY REFERENCES op.captura ON DELETE CASCADE,
  detectada_em  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.imagem_ausente FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();

CREATE FUNCTION op.registrar_imagem_ausente(p_captura bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM op.captura WHERE id = p_captura AND estado = 'processada'
                  AND imagem_chave IS NOT NULL) THEN
    RAISE EXCEPTION 'captura % não tem imagem registrada', p_captura USING ERRCODE = 'STP08';
  END IF;
  INSERT INTO op.imagem_ausente (captura_id) VALUES (p_captura) ON CONFLICT DO NOTHING;
  RETURN FOUND;
END $$;

-- ---------------------------------------------------------------- conclusão de captura (correção)
-- Falha encontrada na implementação do worker: se já existe alerta aberto do mesmo tipo
-- para a posição, a inserção violava o índice único e a captura inteira falhava
-- (e, após 3 tentativas, era recusada). Agora o alerta repetido é ignorado.
CREATE OR REPLACE FUNCTION op.concluir_captura(
  p_captura bigint, p_token uuid, p_imagem_chave text, p_imagem_sha256 text,
  p_metodo bigint, p_observacoes jsonb, p_alertas jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE c op.captura; n_alertas integer;
BEGIN
  IF p_captura IS NULL OR p_token IS NULL OR p_metodo IS NULL OR p_observacoes IS NULL
     OR jsonb_typeof(p_observacoes) IS DISTINCT FROM 'array'
     OR (p_alertas IS NOT NULL AND jsonb_typeof(p_alertas) IS DISTINCT FROM 'array')
     OR jsonb_array_length(p_observacoes) > 500 THEN
    RAISE EXCEPTION 'parâmetros inválidos' USING ERRCODE = 'STP08';
  END IF;
  SELECT * INTO c FROM op.captura WHERE id = p_captura FOR UPDATE;
  IF NOT FOUND OR c.estado IS DISTINCT FROM 'em_processamento' OR c.reserva_token IS DISTINCT FROM p_token THEN
    RAISE EXCEPTION 'reserva perdida para a captura %', p_captura USING ERRCODE = 'STP10';
  END IF;

  INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca)
  SELECT c.id, o.posicao_id, pv.id, p_metodo, o.classe, o.confianca
    FROM jsonb_to_recordset(p_observacoes) AS o(posicao_id bigint, classe text, confianca numeric)
    JOIN op.posicao p ON p.id = o.posicao_id AND p.loja_id = c.loja_id
    LEFT JOIN op.planograma_vigencia pv ON pv.posicao_id = o.posicao_id AND pv.vigencia @> c.capturado_em;
  IF (SELECT count(*) FROM op.observacao_visual WHERE captura_id = c.id AND metodo_id = p_metodo)
     <> jsonb_array_length(p_observacoes) THEN
    RAISE EXCEPTION 'observações com posição inválida ou de outra loja' USING ERRCODE = 'STP02';
  END IF;

  -- todo alerta precisa de observação desta captura na mesma posição
  SELECT count(*) INTO n_alertas
    FROM jsonb_to_recordset(coalesce(p_alertas, '[]')) AS a(posicao_id bigint, tipo text, snapshot_id bigint, versao_regra text)
   WHERE NOT EXISTS (SELECT 1 FROM op.observacao_visual o
                      WHERE o.captura_id = c.id AND o.metodo_id = p_metodo AND o.posicao_id = a.posicao_id);
  IF n_alertas > 0 THEN
    RAISE EXCEPTION 'alerta sem observação correspondente' USING ERRCODE = 'STP02';
  END IF;

  INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
  SELECT c.loja_id, o.posicao_id, pv.produto_id, a.tipo, o.id, a.snapshot_id, a.versao_regra
    FROM jsonb_to_recordset(coalesce(p_alertas, '[]')) AS a(posicao_id bigint, tipo text, snapshot_id bigint, versao_regra text)
    JOIN op.observacao_visual o ON o.captura_id = c.id AND o.metodo_id = p_metodo AND o.posicao_id = a.posicao_id
    JOIN op.planograma_vigencia pv ON pv.id = o.planograma_id
  ON CONFLICT (posicao_id, tipo) WHERE status IN ('aberto', 'em_verificacao') DO NOTHING;

  UPDATE op.captura SET estado = 'processada', imagem_chave = p_imagem_chave, imagem_sha256 = p_imagem_sha256,
         reserva_token = NULL, reserva_por = NULL, reserva_expira_em = NULL
   WHERE id = c.id;
END $$;

-- ---------------------------------------------------------------- privilégios
GRANT SELECT ON op.credencial, op.sessao, op.codigo_externo, op.imagem_ausente TO stp_app;
GRANT INSERT, DELETE ON op.sessao TO stp_app;
GRANT SELECT ON op.codigo_externo, op.imagem_ausente, op.loja, op.transicao_alerta TO stp_worker;
GRANT EXECUTE ON FUNCTION op.registrar_imagem_ausente(bigint) TO stp_worker;
