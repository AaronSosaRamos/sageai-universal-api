-- Tipo de usuario: 'user' = límites diarios y sin creación de asistentes; 'admin' = sin esos límites por defecto.
ALTER TABLE users ADD COLUMN IF NOT EXISTS user_type text NOT NULL DEFAULT 'user';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_user_type_check;
ALTER TABLE users ADD CONSTRAINT users_user_type_check CHECK (user_type IN ('user', 'admin'));

-- Conteo atómico de interacciones por día (UTC) para límite diario
CREATE TABLE IF NOT EXISTS user_daily_interactions (
  user_id text NOT NULL,
  day date NOT NULL,
  interaction_count int NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

CREATE INDEX IF NOT EXISTS idx_user_daily_interactions_day ON user_daily_interactions (day);

-- Incrementa solo si interaction_count < p_limit; retorna si se permitió y el nuevo total
CREATE OR REPLACE FUNCTION consume_daily_interaction(p_user_id text, p_day date, p_limit int)
RETURNS TABLE(allowed boolean, current_count int)
LANGUAGE plpgsql
AS $$
DECLARE
  v_count int;
BEGIN
  INSERT INTO user_daily_interactions (user_id, day, interaction_count)
  VALUES (p_user_id, p_day, 0)
  ON CONFLICT (user_id, day) DO NOTHING;

  SELECT interaction_count INTO v_count
  FROM user_daily_interactions
  WHERE user_id = p_user_id AND day = p_day
  FOR UPDATE;

  IF v_count >= p_limit THEN
    RETURN QUERY SELECT FALSE, v_count;
    RETURN;
  END IF;

  UPDATE user_daily_interactions
  SET interaction_count = interaction_count + 1
  WHERE user_id = p_user_id AND day = p_day
  RETURNING interaction_count INTO v_count;

  RETURN QUERY SELECT TRUE, v_count;
END;
$$;
