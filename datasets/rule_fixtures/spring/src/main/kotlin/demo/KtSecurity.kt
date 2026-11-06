package demo

class KtSecurity {
    fun chain(http: HttpSecurity): SecurityFilterChain {
        http {
            csrf { disable() }
        }
        return http.build()
    }
}
