package demo

class UserRepo(private val jdbc: JdbcTemplate) {
    fun byEmail(email: String) = jdbc.queryForList("select * from users where email = '$email'")

    fun log(token: String) = log.info("issued token=$token")

    fun encoder() = NoOpPasswordEncoder.getInstance()
}
