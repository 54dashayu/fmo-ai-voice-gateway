-module(fmo_audio_guard).
-export([install/0, uninstall/0, on_publish/1, heartbeat/0, active_client/0]).
install() ->
    ok = emqx_hooks:put('message.publish', {?MODULE, on_publish, []}, 1000000),
    case whereis(fmo_audio_guard_heartbeat) of
        undefined -> register(fmo_audio_guard_heartbeat, spawn(?MODULE, heartbeat, []));
        _ -> ok
    end,
    ok.
uninstall() ->
    emqx_hooks:del('message.publish', {?MODULE,on_publish,[]}),
    case whereis(fmo_audio_guard_heartbeat) of undefined -> ok; Pid -> exit(Pid,kill) end,
    file:delete("/run/fmo-audio-hook/ready"), ok.
heartbeat() ->
    Registered = lists:any(fun(C) -> emqx_hooks:callback_action(C) =:= {?MODULE,on_publish,[]} end,
                           emqx_hooks:lookup('message.publish')),
    case Registered of
        true ->
            file:write_file("/run/fmo-audio-hook/ready.tmp", integer_to_binary(erlang:system_time(millisecond))),
            file:rename("/run/fmo-audio-hook/ready.tmp", "/run/fmo-audio-hook/ready");
        false -> file:delete("/run/fmo-audio-hook/ready")
    end,
    timer:sleep(1000), ?MODULE:heartbeat().
active_client() ->
    try
        {ok, Data} = file:read_file("/run/fmo-audio-lease/active"),
        [Until, Client, _Pid] = binary:split(Data, <<"\n">>, [global]),
        Remaining = binary_to_integer(Until) - erlang:system_time(millisecond),
        case Remaining > 0 andalso Remaining =< 6000 of true -> Client; false -> none end
    catch _:_ -> none end.
on_publish(Msg) ->
    case {emqx_message:topic(Msg), active_client()} of
        {<<"FMO/RAW">>, Client} when is_binary(Client) ->
            Local = emqx_message:get_header(peerhost, Msg) =:= {127,0,0,1},
            case emqx_message:from(Msg) =:= Client andalso Local of
                true -> {ok,Msg};
                false -> {stop,emqx_message:set_header(allow_publish,false,Msg)}
            end;
        _ -> {ok,Msg}
    end.
