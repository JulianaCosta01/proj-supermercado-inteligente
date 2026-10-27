-- Schema operacional com as invariantes garantidas pelo próprio banco.
-- Executado como stp_migrator, depois de db/roles.sql.
--
-- Regras para as funções deste schema (verificadas por tests/db/test_v051.py):
-- toda função fixa search_path = pg_catalog, pg_temp e qualifica objetos com op. ou an.;
-- funções nascem sem EXECUTE para PUBLIC; comparações usam IS DISTINCT FROM para que NULL
-- nunca desligue uma checagem.

ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE SCHEMA op;
CREATE SCHEMA an;
REVOKE ALL ON SCHEMA op, an FROM PUBLIC;

CREATE TABLE op.loja (
  id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nome                     text NOT NULL CHECK (length(nome) BETWEEN 1 AND 200),
  origem_dado              text NOT NULL CHECK (origem_dado IN ('real', 'sintetico')),
  fuso                     text NOT NULL DEFAULT 'America/Fortaleza',
  -- idade máxima (em dias) do snapshot de estoque aceito como base de uma divergência
  max_idade_snapshot_dias  integer NOT NULL DEFAULT 1 CHECK (max_idade_snapshot_dias BETWEEN 0 AND 7),
  -- Revogação da autorização bloqueia novas entradas e some das views analíticas
  autorizacao_revogada_em  timestamptz
);

CREATE TABLE op.usuario (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  nome    text NOT NULL,
  papel   text NOT NULL CHECK (papel IN ('repositor', 'gerente', 'analista', 'admin')),
  ativo   boolean NOT NULL DEFAULT true
);

CREATE TABLE op.produto (
  id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id   bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  ean       text CHECK (ean ~ '^[0-9]{8,14}$'),
  descricao text NOT NULL
);

CREATE TABLE op.posicao (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  codigo  text NOT NULL,
  UNIQUE (loja_id, codigo)
);

CREATE TABLE op.planograma_vigencia (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posicao_id bigint NOT NULL REFERENCES op.posicao ON DELETE CASCADE,
  produto_id bigint NOT NULL REFERENCES op.produto ON DELETE CASCADE,
  vigencia   tstzrange NOT NULL CHECK (NOT isempty(vigencia) AND lower_inc(vigencia)),
  UNIQUE (id, posicao_id),
  EXCLUDE USING gist (posicao_id WITH =, vigencia WITH &&)
);

-- O arquivo enviado fica em quarentena (nome = UUID, sem extensão, nunca servido).
-- O worker decodifica, re-codifica e grava a imagem processada; só ela tem chave .jpg.
CREATE TABLE op.captura (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id             bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  sha256_original     char(64) NOT NULL CHECK (sha256_original ~ '^[0-9a-f]{64}$'),
  quarentena_chave    uuid,
  imagem_chave        text CHECK (imagem_chave ~ '^[0-9a-f]{2}/[0-9a-f]{64}\.jpg$'),
  imagem_sha256       char(64) CHECK (imagem_sha256 ~ '^[0-9a-f]{64}$'),
  capturado_em        timestamptz NOT NULL,
  recebido_em         timestamptz NOT NULL DEFAULT now(),
  estado              text NOT NULL DEFAULT 'recebida'
                      CHECK (estado IN ('recebida', 'em_processamento', 'processada', 'recusada')),
  tentativas          integer NOT NULL DEFAULT 0 CHECK (tentativas BETWEEN 0 AND 10),
  reserva_token       uuid,
  reserva_por         text,
  reserva_expira_em   timestamptz,
  motivo_recusa       text,
  imagem_expurgada_em timestamptz,
  UNIQUE (loja_id, sha256_original),
  CHECK (estado <> 'recusada' OR motivo_recusa IS NOT NULL),
  CHECK ((estado = 'em_processamento') = (reserva_token IS NOT NULL AND reserva_expira_em IS NOT NULL)),
  -- processada => hash da imagem processada registrado (sobrevive ao expurgo)
  CHECK (estado <> 'processada' OR imagem_sha256 IS NOT NULL),
  CHECK (imagem_chave IS NULL OR estado = 'processada'),
  CHECK (imagem_expurgada_em IS NULL OR imagem_chave IS NULL)
);
CREATE INDEX captura_fila ON op.captura (recebido_em, id) WHERE estado IN ('recebida', 'em_processamento');

CREATE TABLE op.metodo_ocupacao (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nome            text NOT NULL,
  versao          text NOT NULL,
  artefato_sha256 char(64),
  limiar          numeric(4,3) NOT NULL CHECK (limiar BETWEEN 0 AND 1),
  UNIQUE (nome, versao)
);

CREATE TABLE op.observacao_visual (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  captura_id    bigint NOT NULL REFERENCES op.captura ON DELETE CASCADE,
  posicao_id    bigint NOT NULL,
  planograma_id bigint NOT NULL,
  metodo_id     bigint NOT NULL REFERENCES op.metodo_ocupacao,
  classe        text NOT NULL CHECK (classe IN ('vazio', 'parcial', 'ocupado', 'indeterminado')),
  confianca     numeric(4,3) NOT NULL CHECK (confianca BETWEEN 0 AND 1),
  FOREIGN KEY (planograma_id, posicao_id) REFERENCES op.planograma_vigencia (id, posicao_id) ON DELETE CASCADE,
  UNIQUE (captura_id, posicao_id, metodo_id),
  UNIQUE (id, posicao_id)
);

-- Só importações aceitas entram aqui. Arquivo com erro não grava nenhum snapshot;
-- a tentativa e o relatório ficam em op.importacao_tentativa.
CREATE TABLE op.importacao (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id          bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  tipo             text NOT NULL CHECK (tipo IN ('estoque', 'vendas')),
  sha256           char(64) NOT NULL,
  data_referencia  date NOT NULL,
  linhas_ok        integer NOT NULL CHECK (linhas_ok >= 0),
  linhas_ignoradas integer NOT NULL CHECK (linhas_ignoradas >= 0),  -- produtos fora do cadastro do piloto
  importado_em     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (loja_id, tipo, sha256),
  UNIQUE (id, data_referencia)
);

CREATE TABLE op.importacao_tentativa (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id      bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  tipo         text NOT NULL CHECK (tipo IN ('estoque', 'vendas')),
  sha256       char(64) NOT NULL,
  usuario_id   bigint NOT NULL REFERENCES op.usuario ON DELETE CASCADE,
  recebido_em  timestamptz NOT NULL DEFAULT now(),
  n_erros      integer NOT NULL CHECK (n_erros > 0),
  relatorio    jsonb NOT NULL CHECK (pg_column_size(relatorio) < 1000000)
);

CREATE TABLE op.snapshot_estoque (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  importacao_id   bigint NOT NULL,
  produto_id      bigint NOT NULL REFERENCES op.produto ON DELETE CASCADE,
  data_referencia date NOT NULL,
  quantidade      numeric(12,3) NOT NULL CHECK (quantidade BETWEEN -1000000 AND 1000000),
  -- a data do snapshot é a data declarada do arquivo (um arquivo = uma data)
  FOREIGN KEY (importacao_id, data_referencia) REFERENCES op.importacao (id, data_referencia) ON DELETE CASCADE,
  UNIQUE (importacao_id, produto_id),
  UNIQUE (id, produto_id)
);

CREATE TABLE op.alerta (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id        bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  posicao_id     bigint NOT NULL,
  produto_id     bigint NOT NULL,
  tipo           text NOT NULL CHECK (tipo IN ('D1', 'D2', 'V')),
  observacao_id  bigint NOT NULL,
  snapshot_id    bigint,
  versao_regra   text NOT NULL,
  status         text NOT NULL DEFAULT 'aberto'
                 CHECK (status IN ('aberto', 'em_verificacao', 'confirmado', 'rejeitado', 'resolvido')),
  versao         integer NOT NULL DEFAULT 1,
  criado_em      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (observacao_id, posicao_id) REFERENCES op.observacao_visual (id, posicao_id) ON DELETE CASCADE,
  FOREIGN KEY (snapshot_id, produto_id) REFERENCES op.snapshot_estoque (id, produto_id) ON DELETE CASCADE,
  CHECK (tipo = 'V' OR snapshot_id IS NOT NULL),
  CHECK (versao >= 1)
);
CREATE UNIQUE INDEX alerta_aberto_unico ON op.alerta (posicao_id, tipo)
  WHERE status IN ('aberto', 'em_verificacao');

CREATE TABLE op.transicao_alerta (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  alerta_id   bigint NOT NULL REFERENCES op.alerta ON DELETE CASCADE,
  de_status   text NOT NULL,
  para_status text NOT NULL,
  usuario_id  bigint NOT NULL REFERENCES op.usuario ON DELETE CASCADE,
  causa       text CHECK (causa IN ('reposicao_pendente', 'estoque_fantasma', 'fora_de_posicao',
                                    'planograma_desatualizado', 'outra')),
  motivo      text CHECK (motivo IN ('produto_presente', 'foto_inadequada', 'planograma_errado', 'outro')),
  comentario  text CHECK (length(comentario) <= 2000),
  em          timestamptz NOT NULL DEFAULT now(),
  CHECK (para_status <> 'confirmado' OR causa IS NOT NULL),
  CHECK (para_status <> 'rejeitado' OR motivo IS NOT NULL)
);

CREATE FUNCTION op.somente_insercao() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('stp.expurgo', true) = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'tabela % é somente inserção (%)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'STP01';
END $$;

CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.observacao_visual   FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();
CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.snapshot_estoque    FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();
CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.transicao_alerta    FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();
CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.importacao          FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();
CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.importacao_tentativa FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();

-- Loja com autorização revogada não recebe novas capturas nem importações.
CREATE FUNCTION op.loja_autorizada() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM op.loja WHERE id = NEW.loja_id AND autorizacao_revogada_em IS NOT NULL) THEN
    RAISE EXCEPTION 'loja % com autorização revogada', NEW.loja_id USING ERRCODE = 'STP06';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_autorizada BEFORE INSERT ON op.captura    FOR EACH ROW EXECUTE FUNCTION op.loja_autorizada();
CREATE TRIGGER t_autorizada BEFORE INSERT ON op.importacao FOR EACH ROW EXECUTE FUNCTION op.loja_autorizada();

CREATE FUNCTION op.planograma_guarda() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF current_setting('stp.expurgo', true) = 'on' THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'vigência de planograma não pode ser apagada' USING ERRCODE = 'STP01';
  END IF;
  IF NEW.posicao_id IS DISTINCT FROM OLD.posicao_id OR NEW.produto_id IS DISTINCT FROM OLD.produto_id
     OR lower(NEW.vigencia) IS DISTINCT FROM lower(OLD.vigencia)
     OR NOT upper_inf(OLD.vigencia) OR upper_inf(NEW.vigencia) THEN
    RAISE EXCEPTION 'planograma: só é permitido encerrar uma vigência aberta' USING ERRCODE = 'STP01';
  END IF;
  -- Encerrar a vigência não pode deixar observações já feitas fora dela
  IF EXISTS (SELECT 1 FROM op.observacao_visual o JOIN op.captura c ON c.id = o.captura_id
              WHERE o.planograma_id = NEW.id AND NOT (NEW.vigencia @> c.capturado_em)) THEN
    RAISE EXCEPTION 'planograma: há observações após o novo fim da vigência' USING ERRCODE = 'STP01';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_guarda BEFORE UPDATE OR DELETE ON op.planograma_vigencia
  FOR EACH ROW EXECUTE FUNCTION op.planograma_guarda();

-- A observação só pode apontar para a vigência válida no instante da captura.
CREATE FUNCTION op.observacao_guarda() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM op.captura c JOIN op.planograma_vigencia pv ON pv.id = NEW.planograma_id
                  WHERE c.id = NEW.captura_id AND pv.vigencia @> c.capturado_em) THEN
    RAISE EXCEPTION 'observação: planograma % não vigente no momento da captura', NEW.planograma_id
      USING ERRCODE = 'STP02';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_vigente BEFORE INSERT ON op.observacao_visual
  FOR EACH ROW EXECUTE FUNCTION op.observacao_guarda();

-- Captura: origem imutável; ciclo de estados controlado; imagem só é removida (expurgo).
CREATE FUNCTION op.captura_guarda() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF current_setting('stp.expurgo', true) = 'on' THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'captura não pode ser apagada' USING ERRCODE = 'STP01';
  END IF;
  IF (NEW.loja_id, NEW.sha256_original, NEW.capturado_em, NEW.recebido_em)
     IS DISTINCT FROM (OLD.loja_id, OLD.sha256_original, OLD.capturado_em, OLD.recebido_em) THEN
    RAISE EXCEPTION 'captura: campos de origem são imutáveis' USING ERRCODE = 'STP01';
  END IF;
  IF OLD.estado IN ('processada', 'recusada') AND NEW.estado IS DISTINCT FROM OLD.estado THEN
    RAISE EXCEPTION 'captura: estado % é final', OLD.estado USING ERRCODE = 'STP01';
  END IF;
  IF NEW.estado = 'recebida' AND OLD.estado IS DISTINCT FROM 'recebida' THEN
    RAISE EXCEPTION 'captura: estado não volta para recebida' USING ERRCODE = 'STP01';
  END IF;
  IF NEW.tentativas < OLD.tentativas THEN
    RAISE EXCEPTION 'captura: tentativas não diminuem' USING ERRCODE = 'STP01';
  END IF;
  IF OLD.imagem_sha256 IS NOT NULL AND NEW.imagem_sha256 IS DISTINCT FROM OLD.imagem_sha256 THEN
    RAISE EXCEPTION 'captura: hash da imagem processada é imutável' USING ERRCODE = 'STP01';
  END IF;
  IF OLD.imagem_chave IS NOT NULL AND NEW.imagem_chave IS DISTINCT FROM OLD.imagem_chave
     AND NEW.imagem_chave IS NOT NULL THEN
    RAISE EXCEPTION 'captura: imagem só pode ser removida (expurgo)' USING ERRCODE = 'STP01';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_guarda BEFORE UPDATE OR DELETE ON op.captura
  FOR EACH ROW EXECUTE FUNCTION op.captura_guarda();

-- Alerta: produto coerente com o planograma; snapshot fresco e o mais recente; evidências imutáveis.
CREATE FUNCTION op.alerta_guarda() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_produto bigint; v_dia_captura date; v_max integer; v_snap_data date; v_snap_imp timestamptz;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT pv.produto_id, (c.capturado_em AT TIME ZONE l.fuso)::date, l.max_idade_snapshot_dias
      INTO v_produto, v_dia_captura, v_max
      FROM op.observacao_visual o
      JOIN op.planograma_vigencia pv ON pv.id = o.planograma_id
      JOIN op.captura c ON c.id = o.captura_id
      JOIN op.loja l ON l.id = c.loja_id
     WHERE o.id = NEW.observacao_id;
    IF v_produto IS DISTINCT FROM NEW.produto_id THEN
      RAISE EXCEPTION 'alerta: produto % não é o esperado pelo planograma da observação (%)',
        NEW.produto_id, v_produto USING ERRCODE = 'STP02';
    END IF;
    IF NEW.status IS DISTINCT FROM 'aberto' OR NEW.versao IS DISTINCT FROM 1 THEN
      RAISE EXCEPTION 'alerta deve nascer aberto' USING ERRCODE = 'STP02';
    END IF;
    -- Divergência só com snapshot fresco e sem snapshot mais novo disponível
    IF NEW.tipo IN ('D1', 'D2') THEN
      SELECT s.data_referencia, i.importado_em INTO v_snap_data, v_snap_imp
        FROM op.snapshot_estoque s JOIN op.importacao i ON i.id = s.importacao_id
       WHERE s.id = NEW.snapshot_id;
      IF v_snap_data > v_dia_captura OR v_snap_data < v_dia_captura - v_max THEN
        RAISE EXCEPTION 'alerta: snapshot de % fora da janela aceita para a captura de %', v_snap_data, v_dia_captura
          USING ERRCODE = 'STP07';
      END IF;
      IF EXISTS (SELECT 1 FROM op.snapshot_estoque s JOIN op.importacao i ON i.id = s.importacao_id
                  WHERE s.produto_id = NEW.produto_id AND s.data_referencia <= v_dia_captura
                    AND (s.data_referencia, i.importado_em) > (v_snap_data, v_snap_imp)) THEN
        RAISE EXCEPTION 'alerta: existe snapshot mais recente para o produto %', NEW.produto_id
          USING ERRCODE = 'STP07';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF current_setting('stp.expurgo', true) = 'on' THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'alerta não pode ser apagado' USING ERRCODE = 'STP01';
  END IF;
  IF (NEW.loja_id, NEW.posicao_id, NEW.produto_id, NEW.tipo, NEW.observacao_id, NEW.snapshot_id, NEW.versao_regra, NEW.criado_em)
     IS DISTINCT FROM
     (OLD.loja_id, OLD.posicao_id, OLD.produto_id, OLD.tipo, OLD.observacao_id, OLD.snapshot_id, OLD.versao_regra, OLD.criado_em) THEN
    RAISE EXCEPTION 'alerta: evidências são imutáveis' USING ERRCODE = 'STP01';
  END IF;
  IF current_setting('stp.em_transicao', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'alerta: status só muda por op.registrar_transicao' USING ERRCODE = 'STP01';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_guarda BEFORE INSERT OR UPDATE OR DELETE ON op.alerta
  FOR EACH ROW EXECUTE FUNCTION op.alerta_guarda();

CREATE FUNCTION op.registrar_transicao(
  p_alerta bigint, p_versao_esperada integer, p_para text, p_usuario bigint,
  p_causa text DEFAULT NULL, p_motivo text DEFAULT NULL, p_comentario text DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a op.alerta; u op.usuario; nova_versao integer;
BEGIN
  -- Parâmetros obrigatórios; NULL não pode desligar nenhuma checagem
  IF p_alerta IS NULL OR p_versao_esperada IS NULL OR p_para IS NULL OR p_usuario IS NULL THEN
    RAISE EXCEPTION 'parâmetros obrigatórios ausentes' USING ERRCODE = 'STP08';
  END IF;
  SELECT * INTO u FROM op.usuario WHERE id = p_usuario;
  IF NOT FOUND OR u.ativo IS NOT TRUE THEN
    RAISE EXCEPTION 'usuário inexistente ou inativo' USING ERRCODE = 'STP03';
  END IF;
  SELECT * INTO a FROM op.alerta WHERE id = p_alerta FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'alerta inexistente' USING ERRCODE = 'STP04'; END IF;
  IF a.loja_id IS DISTINCT FROM u.loja_id THEN
    RAISE EXCEPTION 'usuário de outra loja' USING ERRCODE = 'STP03';
  END IF;
  IF a.versao IS DISTINCT FROM p_versao_esperada THEN
    RAISE EXCEPTION 'conflito: alerta % está na versão %, esperado %', a.id, a.versao, p_versao_esperada
      USING ERRCODE = 'STP09';
  END IF;
  IF NOT ((a.status, p_para) IN (('aberto', 'em_verificacao'), ('aberto', 'confirmado'), ('aberto', 'rejeitado'),
                                 ('em_verificacao', 'confirmado'), ('em_verificacao', 'rejeitado'),
                                 ('confirmado', 'resolvido'))) THEN
    RAISE EXCEPTION 'transição % -> % não permitida', a.status, p_para USING ERRCODE = 'STP05';
  END IF;
  IF p_para IN ('confirmado', 'rejeitado') AND u.papel NOT IN ('gerente', 'admin') THEN
    RAISE EXCEPTION 'papel % não pode %', u.papel, p_para USING ERRCODE = 'STP03';
  END IF;

  PERFORM pg_catalog.set_config('stp.em_transicao', 'on', true);
  UPDATE op.alerta SET status = p_para, versao = versao + 1 WHERE id = a.id RETURNING versao INTO nova_versao;
  PERFORM pg_catalog.set_config('stp.em_transicao', 'off', true);

  INSERT INTO op.transicao_alerta (alerta_id, de_status, para_status, usuario_id, causa, motivo, comentario)
  VALUES (a.id, a.status, p_para, u.id, p_causa, p_motivo, p_comentario);
  RETURN nova_versao;
END $$;

-- Reserva curta: a transação só marca a captura e devolve um token. O processamento
-- pesado acontece fora de qualquer transação. Reserva vencida volta para a fila.
CREATE FUNCTION op.reivindicar_captura(p_worker text, p_lease interval)
RETURNS TABLE (captura_id bigint, token uuid, quarentena_chave uuid, tentativa integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_id bigint;
BEGIN
  IF p_worker IS NULL OR p_lease IS NULL OR p_lease < interval '1 second' OR p_lease > interval '30 minutes' THEN
    RAISE EXCEPTION 'reserva inválida' USING ERRCODE = 'STP08';
  END IF;
  -- capturas que esgotaram as tentativas viram recusadas (evita item venenoso em loop)
  UPDATE op.captura SET estado = 'recusada', motivo_recusa = 'falha_processamento',
         reserva_token = NULL, reserva_por = NULL, reserva_expira_em = NULL
   WHERE tentativas >= 3
     AND (estado = 'recebida' OR (estado = 'em_processamento' AND reserva_expira_em < now()));

  SELECT c.id INTO v_id FROM op.captura c
   WHERE c.estado = 'recebida' OR (c.estado = 'em_processamento' AND c.reserva_expira_em < now())
   ORDER BY c.recebido_em, c.id
   FOR UPDATE SKIP LOCKED LIMIT 1;
  IF v_id IS NULL THEN RETURN; END IF;

  RETURN QUERY
  UPDATE op.captura c SET estado = 'em_processamento', tentativas = c.tentativas + 1,
         reserva_token = gen_random_uuid(), reserva_por = p_worker, reserva_expira_em = now() + p_lease
   WHERE c.id = v_id
  RETURNING c.id, c.reserva_token, c.quarentena_chave, c.tentativas;
END $$;

-- Grava, numa única transação, a imagem processada, todas as observações e alertas.
-- Só aceita o resultado de quem tem o token atual (cerca contra worker atrasado).
CREATE FUNCTION op.concluir_captura(
  p_captura bigint, p_token uuid, p_imagem_chave text, p_imagem_sha256 text,
  p_metodo bigint, p_observacoes jsonb, p_alertas jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE c op.captura;
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
  -- (planograma_id NULL ou posição de outra loja => erro de NOT NULL ou contagem abaixo)
  IF (SELECT count(*) FROM op.observacao_visual WHERE captura_id = c.id AND metodo_id = p_metodo)
     <> jsonb_array_length(p_observacoes) THEN
    RAISE EXCEPTION 'observações com posição inválida ou de outra loja' USING ERRCODE = 'STP02';
  END IF;

  INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
  SELECT c.loja_id, o.posicao_id, pv.produto_id, a.tipo, o.id, a.snapshot_id, a.versao_regra
    FROM jsonb_to_recordset(coalesce(p_alertas, '[]')) AS a(posicao_id bigint, tipo text, snapshot_id bigint, versao_regra text)
    JOIN op.observacao_visual o ON o.captura_id = c.id AND o.metodo_id = p_metodo AND o.posicao_id = a.posicao_id
    JOIN op.planograma_vigencia pv ON pv.id = o.planograma_id;
  IF (SELECT count(*) FROM op.alerta al JOIN op.observacao_visual o ON o.id = al.observacao_id
       WHERE o.captura_id = c.id AND o.metodo_id = p_metodo) <> jsonb_array_length(coalesce(p_alertas, '[]')) THEN
    RAISE EXCEPTION 'alerta sem observação correspondente' USING ERRCODE = 'STP02';
  END IF;

  UPDATE op.captura SET estado = 'processada', imagem_chave = p_imagem_chave, imagem_sha256 = p_imagem_sha256,
         reserva_token = NULL, reserva_por = NULL, reserva_expira_em = NULL
   WHERE id = c.id;
END $$;

CREATE FUNCTION op.recusar_captura(p_captura bigint, p_token uuid, p_motivo text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF p_motivo IS NULL OR length(p_motivo) > 200 THEN
    RAISE EXCEPTION 'motivo obrigatório' USING ERRCODE = 'STP08';
  END IF;
  UPDATE op.captura SET estado = 'recusada', motivo_recusa = p_motivo,
         reserva_token = NULL, reserva_por = NULL, reserva_expira_em = NULL
   WHERE id = p_captura AND estado = 'em_processamento' AND reserva_token IS NOT DISTINCT FROM p_token
     AND p_token IS NOT NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'reserva perdida para a captura %', p_captura USING ERRCODE = 'STP10';
  END IF;
END $$;

CREATE PROCEDURE op.excluir_loja(p_loja bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  PERFORM pg_catalog.set_config('stp.expurgo', 'on', true);
  DELETE FROM op.loja WHERE id = p_loja;
  PERFORM pg_catalog.set_config('stp.expurgo', 'off', true);
END $$;

CREATE VIEW an.fato_alerta AS
SELECT a.id AS alerta_id, a.loja_id, a.tipo, a.status, a.criado_em,
       (SELECT min(t.em) FROM op.transicao_alerta t WHERE t.alerta_id = a.id) - a.criado_em AS tempo_ate_verificacao,
       (SELECT t.causa FROM op.transicao_alerta t WHERE t.alerta_id = a.id AND t.para_status = 'confirmado') AS causa
  FROM op.alerta a JOIN op.loja l ON l.id = a.loja_id
 WHERE l.origem_dado = 'real' AND l.autorizacao_revogada_em IS NULL;

CREATE VIEW an.frescor_dados AS
SELECT l.id AS loja_id,
       (SELECT max(c.capturado_em) FROM op.captura c WHERE c.loja_id = l.id) AS ultima_foto,
       (SELECT max(i.importado_em) FROM op.importacao i WHERE i.loja_id = l.id) AS ultima_importacao
  FROM op.loja l WHERE l.origem_dado = 'real' AND l.autorizacao_revogada_em IS NULL;

GRANT USAGE ON SCHEMA op TO stp_app, stp_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA op TO stp_app;
GRANT INSERT ON op.captura, op.importacao, op.snapshot_estoque, op.importacao_tentativa TO stp_app;
GRANT EXECUTE ON FUNCTION op.registrar_transicao(bigint, integer, text, bigint, text, text, text) TO stp_app;

-- O worker não escreve em tabelas diretamente; só pelas três funções da fila.
GRANT SELECT ON op.captura, op.posicao, op.planograma_vigencia, op.metodo_ocupacao,
                op.snapshot_estoque, op.importacao, op.produto, op.observacao_visual, op.alerta TO stp_worker;
GRANT EXECUTE ON FUNCTION op.reivindicar_captura(text, interval) TO stp_worker;
GRANT EXECUTE ON FUNCTION op.concluir_captura(bigint, uuid, text, text, bigint, jsonb, jsonb) TO stp_worker;
GRANT EXECUTE ON FUNCTION op.recusar_captura(bigint, uuid, text) TO stp_worker;

GRANT USAGE ON SCHEMA an TO stp_bi;
GRANT SELECT ON ALL TABLES IN SCHEMA an TO stp_bi;
