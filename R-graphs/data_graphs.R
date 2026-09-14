library(readxl)
library(dplyr)
library(stringr)
library(tidyr)
library(purrr)
library(ggplot2)

caminho_arquivo <- "D:/UEM/projeto_yandre/TilapiaFreshAI/data-raw/dados-ph.xlsx"

# Remove espacos extras e corrige erros "O4" -> "04"
limpar_rotulos <- function(x) {
  x <- str_squish(as.character(x))
  x <- str_replace(x, "^O4", "04")
  x
}

# Converte texto de temperatura ("19,4 C") em numero (19.4)
limpar_temperatura <- function(x) {
  x <- str_replace(x, ",", ".")
  as.numeric(str_extract(x, "[0-9.]+"))
}

parse_amostra <- function(rotulo) {
  rotulo <- limpar_rotulos(rotulo)
  
  if (str_detect(str_to_lower(rotulo), "^controle")) {
    replica <- as.integer(str_extract(rotulo, "\\d+"))
    return(tibble(
      grupo = "Controle",
      oleo = "Controle",
      concentracao = 0,
      replica = replica,
      amostra_raw = rotulo
    ))
  }
  
  m <- str_match(rotulo, "^([0-9.]+)\\s*([A-Z.]+)\\s*(\\d+)$")
  conc_raw <- m[, 2]
  oleo_code <- m[, 3]
  replica <- as.integer(m[, 4])
  
  conc_map <- c("04" = 0.4, "08" = 0.8, "1" = 1.0)
  oleo_map <- c(
    "OT" = "Tomilho",
    "OO" = "Orégano",
    "OC" = "Canela",
    "O.A." = "Alecrim",
    "O.A" = "Alecrim"
  )
  
  tibble(
    grupo = "Tratamento",
    oleo = unname(oleo_map[oleo_code]),
    concentracao = unname(conc_map[conc_raw]),
    replica = replica,
    amostra_raw = rotulo
  )
}

processar_aba <- function(caminho, aba) {
  
  bruto <- read_excel(caminho, sheet = aba, col_names = FALSE)
  
  linha_cabecalho <- bruto[1, ]
  linha_ur <- bruto[2, ]
  linha_temp_in <- bruto[3, ]
  linha_temp_out <- bruto[4, ]
  
  # Colunas onde come???a cada bloco "Dia X"
  col_dias <- which(str_detect(as.character(linha_cabecalho), "^Dia"))
  
  linhas_amostra <- 8:37  # onde ficam as amostras (fixo na planilha)
  
  map_dfr(col_dias, function(col) {
    
    dia_num <- as.integer(str_extract(linha_cabecalho[[col]], "\\d+"))
    ur <- as.numeric(linha_ur[[col + 1]])
    t_in <- limpar_temperatura(linha_temp_in[[col + 1]])
    t_out <- limpar_temperatura(linha_temp_out[[col + 1]])
    
    amostras <- bruto[linhas_amostra, col][[1]]
    phs <- bruto[linhas_amostra, col + 1][[1]]
    
    tibble(amostra = amostras, ph_raw = phs) %>%
      filter(!is.na(amostra)) %>%
      mutate(
        dia = dia_num,
        UR = ur,
        temp_in = t_in,
        temp_out = t_out,
        ph = as.numeric(str_replace(as.character(ph_raw), ",", "."))
      ) %>%
      bind_cols(map_dfr(.$amostra, parse_amostra)) %>%
      select(dia, grupo, oleo, concentracao, replica, ph, UR, temp_in, temp_out, amostra_raw)
  })
}

# ------------------------------------------------------------
# 3. Junta as duas abas (Dia 0-5 e Dia 6-10)
# ------------------------------------------------------------

dados_ph <- bind_rows(
  processar_aba(caminho_arquivo, "Dia 0 - 5"),
  processar_aba(caminho_arquivo, "Dia 6 - 10")
) %>%
  arrange(dia, grupo, oleo, concentracao, replica)

# Checagem
stopifnot(nrow(dados_ph) == 286)

# ------------------------------------------------------------
# 4. Tabela separada de peso
# ------------------------------------------------------------

bruto_dia10 <- read_excel(caminho_arquivo, sheet = "Dia 6 - 10", col_names = FALSE)

dados_peso <- tibble(
  amostra_raw   = limpar_rotulos(bruto_dia10[8:37, 1][[1]]),
  peso_inicial  = as.numeric(str_remove(bruto_dia10[8:37, 16][[1]], "g")),
  peso_final    = as.numeric(str_remove(bruto_dia10[8:37, 17][[1]], "g"))
) %>%
  filter(!is.na(amostra_raw)) %>%
  bind_cols(map_dfr(.$amostra_raw, parse_amostra) %>% select(-amostra_raw)) %>%
  mutate(perda_percentual = (peso_inicial - peso_final) / peso_inicial * 100) %>%
  select(grupo, oleo, concentracao, replica, peso_inicial, peso_final, perda_percentual, amostra_raw)

# ------------------------------------------------------------
# 5. Conferencia rapida
# ------------------------------------------------------------

glimpse(dados_ph)
glimpse(dados_peso)


# ============================================================
# 6. GRAFICOS
# ============================================================

# Grafico 1: Evolucaoo do pH medio por Tratamento ao longo dos Dias
dados_ph_resumo <- dados_ph %>%
  group_by(dia, oleo) %>%
  summarise(ph_medio = mean(ph, na.rm = TRUE), .groups = 'drop')

grafico_ph <- ggplot(dados_ph_resumo, aes(x = dia, y = ph_medio, color = oleo, group = oleo)) +
  geom_line(size = 1.2) +
  geom_point(size = 3) +
  scale_x_continuous(breaks = unique(dados_ph$dia)) +
  theme_minimal() +
  labs(title = "Evolução Média do pH da Tilápia Pós-Abate",
       subtitle = "Comparação entre os diferentes óleos Essenciais e Controle",
       x = "Dias Pós-Abate",
       y = "pH Médio",
       color = "Tratamento") +
  theme(plot.title = element_text(face = "bold", size = 14))

print(grafico_ph)

# Grafico 2: Boxplot da Perda de Peso Percentual no Dia 10
grafico_peso <- ggplot(dados_peso, aes(x = oleo, y = perda_percentual, fill = oleo)) +
  geom_boxplot(alpha = 0.7) +
  theme_minimal() +
  labs(title = "Perda de Peso (%) dos Filés após 10 Dias",
       x = "Tratamento (óleo)",
       y = "Perda de Peso Percentual (%)",
       fill = "Tratamento") +
  theme(plot.title = element_text(face = "bold", size = 14),
        legend.position = "none"
  )

print(grafico_peso)
