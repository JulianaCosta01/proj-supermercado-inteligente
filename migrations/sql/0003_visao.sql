-- Geometria das faces de gôndola, marcadores ArUco e resultado do alinhamento.

CREATE TABLE op.face_gondola (
  id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loja_id              bigint NOT NULL REFERENCES op.loja ON DELETE CASCADE,
  codigo               text NOT NULL CHECK (codigo ~ '^[A-Za-z0-9._-]{1,40}$'),
  largura_mm           integer NOT NULL CHECK (largura_mm BETWEEN 100 AND 5000),
  altura_mm            integer NOT NULL CHECK (altura_mm BETWEEN 100 AND 3000),
  aruco_dicionario     text NOT NULL DEFAULT 'DICT_4X4_50' CHECK (aruco_dicionario IN ('DICT_4X4_50', 'DICT_5X5_100')),
  marcador_tamanho_mm  integer NOT NULL CHECK (marcador_tamanho_mm BETWEEN 20 AND 300),
  referencia_sha256    char(64) CHECK (referencia_sha256 ~ '^[0-9a-f]{64}$'),
  UNIQUE (loja_id, codigo),
  UNIQUE (id, loja_id)
);

-- Um id de marcador identifica uma única face na loja: sem isso, a foto poderia ser
-- associada à gôndola errada.
CREATE TABLE op.face_marcador (
  face_id     bigint NOT NULL,
  loja_id     bigint NOT NULL,
  marcador_id integer NOT NULL CHECK (marcador_id BETWEEN 0 AND 99),
  x_mm        integer NOT NULL CHECK (x_mm >= 0),
  y_mm        integer NOT NULL CHECK (y_mm >= 0),
  FOREIGN KEY (face_id, loja_id) REFERENCES op.face_gondola (id, loja_id) ON DELETE CASCADE,
  PRIMARY KEY (face_id, marcador_id),
  UNIQUE (loja_id, marcador_id)
);

ALTER TABLE op.posicao
  ADD COLUMN face_id    bigint REFERENCES op.face_gondola ON DELETE CASCADE,
  ADD COLUMN x_mm       integer,
  ADD COLUMN y_mm       integer,
  ADD COLUMN largura_mm integer,
  ADD COLUMN altura_mm  integer,
  ADD CONSTRAINT posicao_geometria_completa CHECK (
    (face_id IS NULL AND x_mm IS NULL AND y_mm IS NULL AND largura_mm IS NULL AND altura_mm IS NULL)
    OR (face_id IS NOT NULL AND x_mm >= 0 AND y_mm >= 0 AND largura_mm >= 20 AND altura_mm >= 20));

-- A posição tem de caber na face e pertencer à mesma loja.
CREATE FUNCTION op.posicao_geometria() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.face_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM op.face_gondola f WHERE f.id = NEW.face_id AND f.loja_id = NEW.loja_id
          AND NEW.x_mm + NEW.largura_mm <= f.largura_mm AND NEW.y_mm + NEW.altura_mm <= f.altura_mm) THEN
    RAISE EXCEPTION 'posição % fora da face ou de outra loja', NEW.codigo USING ERRCODE = 'STP02';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER t_geometria BEFORE INSERT OR UPDATE ON op.posicao
  FOR EACH ROW EXECUTE FUNCTION op.posicao_geometria();

-- Resultado do alinhamento de cada captura processada (somente inserção).
CREATE TABLE op.captura_face (
  captura_id            bigint PRIMARY KEY REFERENCES op.captura ON DELETE CASCADE,
  face_id               bigint NOT NULL REFERENCES op.face_gondola ON DELETE CASCADE,
  marcadores_detectados smallint NOT NULL CHECK (marcadores_detectados BETWEEN 0 AND 4),
  situacao              text NOT NULL CHECK (situacao IN ('alinhada', 'marcadores_insuficientes', 'alinhamento_impreciso',
                                                          'resolucao_insuficiente', 'exposicao_inadequada', 'desfocada', 'sem_referencia')),
  erro_alinhamento_mm   numeric(6,2),
  metricas              jsonb NOT NULL DEFAULT '{}' CHECK (pg_column_size(metricas) < 8000)
);
CREATE TRIGGER t_ro BEFORE UPDATE OR DELETE ON op.captura_face FOR EACH ROW EXECUTE FUNCTION op.somente_insercao();

-- Conclusão com o resultado do alinhamento, na mesma transação das observações.
CREATE FUNCTION op.concluir_captura(
  p_captura bigint, p_token uuid, p_imagem_chave text, p_imagem_sha256 text,
  p_metodo bigint, p_observacoes jsonb, p_alertas jsonb,
  p_face bigint, p_situacao text, p_marcadores smallint, p_erro_mm numeric, p_metricas jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_loja bigint;
BEGIN
  SELECT loja_id INTO v_loja FROM op.captura WHERE id = p_captura;
  IF p_face IS NULL OR NOT EXISTS (SELECT 1 FROM op.face_gondola WHERE id = p_face AND loja_id = v_loja) THEN
    RAISE EXCEPTION 'face inválida para a captura %', p_captura USING ERRCODE = 'STP02';
  END IF;
  -- observações só de posições desta face
  IF EXISTS (SELECT 1 FROM jsonb_to_recordset(p_observacoes) AS o(posicao_id bigint)
              JOIN op.posicao p ON p.id = o.posicao_id
             WHERE p.face_id IS DISTINCT FROM p_face) THEN
    RAISE EXCEPTION 'observação de posição fora da face %', p_face USING ERRCODE = 'STP02';
  END IF;
  PERFORM op.concluir_captura(p_captura, p_token, p_imagem_chave, p_imagem_sha256, p_metodo, p_observacoes, p_alertas);
  INSERT INTO op.captura_face (captura_id, face_id, marcadores_detectados, situacao, erro_alinhamento_mm, metricas)
  VALUES (p_captura, p_face, p_marcadores, p_situacao, p_erro_mm, coalesce(p_metricas, '{}'));
END $$;

GRANT SELECT ON op.face_gondola, op.face_marcador, op.captura_face TO stp_app, stp_worker;
GRANT EXECUTE ON FUNCTION op.concluir_captura(bigint, uuid, text, text, bigint, jsonb, jsonb, bigint, text, smallint, numeric, jsonb) TO stp_worker;

-- Método de visão B1 (sem artefato treinado: a versão identifica o código e os limiares).
INSERT INTO op.metodo_ocupacao (nome, versao, limiar) VALUES ('B1-referencia', '1.0.0', 0.80);
