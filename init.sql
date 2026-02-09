USE mcpdb;

CREATE TABLE IF NOT EXISTS users (
  user_id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  display_name VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_login TIMESTAMP NULL,
  is_active BOOLEAN DEFAULT TRUE,
  INDEX idx_email (email),
  INDEX idx_is_active (is_active)
);

CREATE TABLE IF NOT EXISTS tokens (
  token_id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  access_token VARCHAR(512) NOT NULL,
  refresh_token VARCHAR(512) NOT NULL,
  token_expiry TIMESTAMP NOT NULL,
  scopes TEXT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_tokens_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  INDEX idx_tokens_user_id (user_id),
  INDEX idx_token_expiry (token_expiry),
  INDEX idx_access_token (access_token(255))
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id INT AUTO_INCREMENT PRIMARY KEY,
  session_token VARCHAR(512) NOT NULL UNIQUE,
  user_id INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL,
  ip_address VARCHAR(45),
  user_agent TEXT,
  CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
  INDEX idx_sessions_user_id (user_id),
  INDEX idx_expires_at (expires_at),
  INDEX idx_session_token (session_token(255))
);

CREATE TABLE IF NOT EXISTS audit_logs (
  log_id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  action VARCHAR(255) NOT NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  success BOOLEAN DEFAULT TRUE,
  ip_address VARCHAR(45),
  source VARCHAR(255),
  details JSON,
  CONSTRAINT fk_audit_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
  INDEX idx_audit_user_id (user_id),
  INDEX idx_timestamp (timestamp),
  INDEX idx_action (action)
);
